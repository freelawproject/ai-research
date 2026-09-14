"""Prepare seeder inputs: tagged opinion text + inventory per cluster, paginated
into chat-completions batch records (Kimi / GLM body shape).

Sources (per cluster, first available wins):
  revised_html/{cid}.html   annotator export (gold coreference)  -> source=gold
                            ONLY when grouping_overrides/{cid}.json marks the
                            cluster reviewed (cluster_done, or
                            treatments_reviewed for the benchmark) — the
                            viewer writes a revised_html on page load, before
                            any review, so the file alone is not gold
  opinion_html/{cid}.json   raw CourtListener html_with_citations -> source=eyecite

Two roots are supported:
  --root data/annotator            the triage annotator instance
                                   (data/opinion_html, data/revised_html,
                                   overrides/citing_metadata.csv); --split
                                   train|dev|test|all picks the set (train =
                                   clusters not in inputs/splits.csv)
  --root benchmark --split dev     the citator-benchmark repo, clusters from
                                   inputs/splits.csv (dev or test)

Outputs under --out (default data/seed/<tag>/):
  tagged/{cid}.txt         tagged opinion text (shared with build_windows.py)
  inventory/{cid}.json     inventory list
  input.jsonl              one Bedrock batch record per page
  manifest.json            per cluster: source, pages, inventory size, chars

    python prepare_seed_inputs.py --root data/annotator --model kimi --tag pilot_kimi
    python prepare_seed_inputs.py --root benchmark --split dev --model kimi --tag dev_kimi
"""
import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "citator-pipeline", "utils"))
from preprocess import split_opinion_to_pages  # noqa: E402

from seed_prompt import SEEDER_SCHEMA, VERSION, build_seeder_prompt, render_user_message  # noqa: E402
from tagged_text import from_opinion_html, from_revised_html, inventory_block, load_json  # noqa: E402

BENCH = Path("/Users/rachel/Desktop/flp/citator-benchmark/data")
SPLITS = Path(__file__).parent / "inputs" / "splits.csv"
MODELS = {
    # model id, max output tokens, default page size (chars)
    "kimi": ("moonshotai.kimi-k2.5", 16000, 400_000),
    "glm5": ("zai.glm-5", 32000, 400_000),
    "glm47": ("zai.glm-4.7", 4096, 250_000),
    "kimi-thinking": ("moonshot.kimi-k2-thinking", 16000, 400_000),
}
TEMPERATURE = 0.1
_GROUP_IN_PAGE = re.compile(r'<citedCase group="(\d+)">')


def load_clusters(root, split):
    """[{cluster_id, case_name, court, year, revised_html, opinion_html}]"""
    out = []
    if root == "benchmark":
        meta = {r["citing_cluster_id"]: r for r in csv.DictReader(open(BENCH / "citing_cases.csv"))}
        ids = [r["cluster_id"] for r in csv.DictReader(open(SPLITS)) if r["split"] == split]
        for cid in ids:
            m = meta.get(cid, {})
            out.append({"cluster_id": cid, "case_name": m.get("case_name", ""), "court": m.get("court", ""),
                        "year": m.get("year", ""),
                        "revised_html": BENCH / "revised_html" / f"{cid}.html",
                        "opinion_html": BENCH / "opinion_html" / f"{cid}.json",
                        "overrides": BENCH / "grouping_overrides" / f"{cid}.json",
                        "gate": ("treatments_reviewed", "cluster_done")})
    else:
        root = Path(root)
        split_of = {r["cluster_id"]: r["split"] for r in csv.DictReader(open(SPLITS))} if SPLITS.exists() else {}
        for r in csv.DictReader(open(root / "overrides" / "citing_metadata.csv")):
            cid = r["cluster_id"]
            sp = split_of.get(cid, "train")
            if split != "all" and sp != split:
                continue
            out.append({"cluster_id": cid, "case_name": r.get("case_name", ""), "court": r.get("court", ""),
                        "year": r.get("year", ""),
                        "revised_html": root / "data" / "revised_html" / f"{cid}.html",
                        "opinion_html": root / "data" / "opinion_html" / f"{cid}.json",
                        "overrides": root / "data" / "grouping_overrides" / f"{cid}.json",
                        "gate": ("cluster_done",)})
    return out


def is_reviewed(c):
    if not c["overrides"].exists():
        return False
    ov = load_json(c["overrides"])
    return any(ov.get(k) for k in c["gate"])


def tag_cluster(c):
    if c["revised_html"].exists() and is_reviewed(c):
        return (*from_revised_html(open(c["revised_html"], encoding="utf-8").read()), "gold")
    if c["opinion_html"].exists():
        return (*from_opinion_html(load_json(c["opinion_html"])), "eyecite")
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/annotator", help="annotator root, or 'benchmark'")
    ap.add_argument("--split", default="train", help="train | dev | test | all (benchmark root: dev | test)")
    ap.add_argument("--model", choices=sorted(MODELS), default="kimi")
    ap.add_argument("--page-chars", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    model_id, max_tokens, page_default = MODELS[a.model]
    page_chars = a.page_chars or page_default
    tag = a.tag or f"{a.split}_{a.model}"
    out = Path(a.out or f"data/seed/{tag}")
    (out / "tagged").mkdir(parents=True, exist_ok=True)
    (out / "inventory").mkdir(exist_ok=True)

    system_prompt = build_seeder_prompt()
    clusters = load_clusters(a.root, a.split)
    if a.limit:
        clusters = clusters[:a.limit]
    manifest, records = {}, []
    n_gold = n_eye = n_skip = 0
    for c in clusters:
        text, inv, source = tag_cluster(c)
        if text is None:
            n_skip += 1
            continue
        n_gold += source == "gold"
        n_eye += source == "eyecite"
        cid = c["cluster_id"]
        (out / "tagged" / f"{cid}.txt").write_text(text, encoding="utf-8")
        (out / "inventory" / f"{cid}.json").write_text(json.dumps(inv, indent=1), encoding="utf-8")
        pages = split_opinion_to_pages(text, page_size=page_chars, overlap=min(20_000, page_chars // 10))
        inv_by_id = {str(e["id"]): e for e in inv}
        citing_case = {"name": c["case_name"], "court": c["court"], "year": c["year"]}
        for i, page in enumerate(pages):
            ids_on_page = sorted({g for g in _GROUP_IN_PAGE.findall(page)}, key=int)
            page_inv = [inv_by_id[g] for g in ids_on_page if g in inv_by_id] if len(pages) > 1 else inv
            user = render_user_message(system_prompt, citing_case, inventory_block(page_inv), page, i, len(pages))
            records.append({"recordId": f"{cid}-p{i}",
                            "modelInput": {"messages": [{"role": "user", "content": user}],
                                           "max_tokens": max_tokens, "temperature": TEMPERATURE}})
        manifest[cid] = {"source": source, "n_pages": len(pages), "n_inventory": len(inv),
                         "chars": len(text), "case_name": c["case_name"], "court": c["court"], "year": c["year"]}

    with open(out / "input.jsonl", "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    json.dump({"tag": tag, "prompt_version": VERSION, "model_id": model_id, "max_tokens": max_tokens,
               "page_chars": page_chars, "temperature": TEMPERATURE, "n_clusters": len(manifest),
               "n_records": len(records), "sources": {"gold": n_gold, "eyecite": n_eye, "skipped": n_skip},
               "schema": SEEDER_SCHEMA, "clusters": manifest},
              open(out / "manifest.json", "w", encoding="utf-8"), indent=1)
    total_chars = sum(len(r["modelInput"]["messages"][0]["content"]) for r in records)
    print(f"{tag}: {len(manifest)} clusters ({n_gold} gold / {n_eye} eyecite / {n_skip} skipped) -> "
          f"{len(records)} records, ~{total_chars / 4 / 1e6:.2f}M input tokens, model {model_id}, "
          f"max_tokens {max_tokens}, page {page_chars:,} chars")
    if len(records) < 100:
        print("  NOTE: Bedrock batch needs >= 100 records per file; run on-demand or pad.")


if __name__ == "__main__":
    main()
