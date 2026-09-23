"""Add the dev/test benchmark citing clusters to the triage annotator root so
they go through the same steps as the training sample (centralia pass,
feed-back, grouping/verify, passage review) in ONE viewer.

From citator-benchmark/data, for every cluster in inputs/splits.csv:
  assignments_long.csv   one row per (citing, cited_ref) pair, expert labels
                         BLANKED (gold stays in the benchmark for evaluation;
                         it must not show in the review UI)
  citing_metadata.csv    court / case name / year, source = benchmark_<split>
  opinion_html/          cached CourtListener html (86 of 383) — the rest are
                         fetched on demand by the viewer / fetch step
  revised_html/          the 74 gold-grouped exports
  grouping_overrides/    the gold grouping work (cluster_done etc.), so it is
                         not redone; never overwrites an existing file
Idempotent: reruns add nothing twice. Clusters whose split is "unused" (the
downsized split) are REMOVED from the root — their rows, metadata and copied
files — so the viewer shows only train/dev/test. The benchmark keeps its own
originals.

    python add_benchmark_clusters.py [--root data/annotator]
"""
import argparse
import csv
import os
import shutil
from datetime import date
from pathlib import Path

FLP = Path(__file__).resolve().parents[4]      # the workspace holding both checkouts
# the citator-benchmark checkout, a sibling of the ai-research checkout;
# CITATOR_BENCH overrides
BENCH = Path(os.environ.get("CITATOR_BENCH", FLP / "citator-benchmark")) / "data"
SPLITS = Path(__file__).parent.parent / "inputs" / "splits.csv"
ASSIGN_FIELDS = ["group", "pass_type", "citing_cluster_id", "cited_cluster_id", "cited_ref", "expert",
                 "label_raw", "label", "notes", "round_zip", "group_zip", "source_file", "ingested",
                 "citing_url", "cited_url", "citing_court_name", "cited_court_name",
                 "cited_case_name_short", "cited_case_name", "cited_citations"]
META_FIELDS = ["cluster_id", "source", "court", "case_name", "year", "num_authorities",
               "num_unmatched_citations", "opinion_text_length"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="data/annotator")
    a = ap.parse_args()
    root = Path(a.root)
    data, overrides = root / "data", root / "overrides"
    all_splits = {r["cluster_id"]: r["split"] for r in csv.DictReader(open(SPLITS))}
    unused = {c for c, sp in all_splits.items() if sp == "unused"}
    splits = {c: sp for c, sp in all_splits.items() if sp != "unused"}
    today = date.today().isoformat()

    # assignments: one blanked row per pair
    apath = data / "assignments_long.csv"
    existing = list(csv.DictReader(open(apath, encoding="utf-8"))) if apath.exists() else []
    n_before = len(existing)
    existing = [r for r in existing if r["citing_cluster_id"] not in unused]
    n_pruned_rows = n_before - len(existing)
    have_pairs = {(r["citing_cluster_id"], r["cited_ref"]) for r in existing}
    added, pairs_by = [], {}
    for r in csv.DictReader(open(BENCH / "assignments_long.csv", encoding="utf-8")):
        cid = r["citing_cluster_id"]
        if cid not in splits:
            continue
        key = (cid, r["cited_ref"])
        pairs_by[cid] = pairs_by.get(cid, 0) + (key not in have_pairs)
        if key in have_pairs:
            continue
        have_pairs.add(key)
        added.append({**{k: r.get(k, "") for k in ASSIGN_FIELDS},
                      "pass_type": "full", "expert": "", "label_raw": "", "label": "", "notes": "",
                      "round_zip": f"benchmark_{splits[cid]}", "source_file": "citator-benchmark/assignments_long.csv",
                      "ingested": today})
    with open(apath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ASSIGN_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(existing + added)

    # citing metadata
    mpath = overrides / "citing_metadata.csv"
    meta = list(csv.DictReader(open(mpath, encoding="utf-8"))) if mpath.exists() else []
    meta = [r for r in meta if r["cluster_id"] not in unused]
    have = {r["cluster_id"] for r in meta}
    bench_meta = {r["citing_cluster_id"]: r for r in csv.DictReader(open(BENCH / "citing_cases.csv", encoding="utf-8"))}
    n_meta = 0
    for cid, sp in splits.items():
        if cid in have:
            continue
        m = bench_meta.get(cid, {})
        meta.append({"cluster_id": cid, "source": f"benchmark_{sp}", "court": m.get("court", ""),
                     "case_name": m.get("case_name", ""), "year": m.get("year", ""),
                     "num_authorities": pairs_by.get(cid, ""), "num_unmatched_citations": "",
                     "opinion_text_length": ""})
        n_meta += 1
    with open(mpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=META_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(meta)

    # files: html, revised html, gold grouping overrides (never overwrite)
    counts = {}
    for sub, ext in (("opinion_html", ".json"), ("revised_html", ".html"), ("grouping_overrides", ".json")):
        (data / sub).mkdir(parents=True, exist_ok=True)
        n = 0
        for cid in splits:
            src, dst = BENCH / sub / f"{cid}{ext}", data / sub / f"{cid}{ext}"
            if src.exists() and not dst.exists():
                shutil.copyfile(src, dst)
                n += 1
        counts[sub] = n
    n_files = 0
    for sub, exts in (("opinion_html", (".json", ".cl.json")), ("revised_html", (".html",)),
                      ("grouping_overrides", (".json",)), ("passage_reviews", (".json",))):
        for cid in unused:
            for ext in exts:
                f = data / sub / f"{cid}{ext}"
                if f.exists():
                    f.unlink()
                    n_files += 1
    print(f"pruned {len(unused)} unused benchmark clusters: {n_pruned_rows} pair rows, {n_files} files")
    print(f"added {len(added)} pair rows, {n_meta} citing clusters "
          f"({sum(1 for v in splits.values() if v == 'dev')} dev / {sum(1 for v in splits.values() if v == 'test')} test); "
          f"copied {counts}")
    missing = [cid for cid in splits if not (data / "opinion_html" / f"{cid}.json").exists()]
    print(f"{len(missing)} clusters still need opinion html from CourtListener (viewer fetches on open; "
          f"or GET /api/opinion/{{cid}} for each)")


if __name__ == "__main__":
    main()
