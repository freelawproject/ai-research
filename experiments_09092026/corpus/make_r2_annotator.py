"""Round-2 blocks -> a citator-benchmark annotator root, so the seeding
harness (`triage/lib/citation_seed.py prepare|apply|score`, `openai_batch.sh run`)
and the viewer work on the 10K set exactly as on the triage sample.

    uv run --no-project python corpus/make_r2_annotator.py            # -> data/annotator_r2/
    uv run --no-project python corpus/make_r2_annotator.py --limit 200  # a subset (pilot)

Steps: (1) unpack data/cl20k_r2/blocks/*.jsonl.gz (pool selected|topup) into a
sampler-shaped dir data/r2_sample/ — opinion_html/{cid}.json payloads
({cluster_id, opinions:[{id,type,source,html}], origin, fetched}),
sample_metadata.csv (court/case_name/year/... from the block rows) and
authority_metadata.csv (cited clusters from the CourtListener links inside
html_with_citations, the same derivation make_annotator_data uses in
--html-only mode); (2) run triage/annotator/make_annotator_data.py on it, which writes
the viewer root (assignments_long.csv, predictions.csv, grouping_overrides/,
overrides/citing_metadata.csv, ...). Re-runnable; never touches
grouping_overrides/ (where seeding edits land).

Then, from experiments_09022026/triage:

    export CITSEED_ANNOT=$PWD/data/annotator_r2        # from the experiment root
    export CITSEED_OUT=$PWD/data/citation_seed_r2
    python3 citation_seed.py prepare --seed-state --ids $(cat $CITSEED_ANNOT/ids.txt)
    bash openai_batch.sh run --name r2_gpt --model gpt-5.6-luna --prompt prompts/triage_citation_fixer_v8g.md \\
         --effort high --candidates --workers 32 --ids $(cat $CITSEED_ANNOT/ids.txt)
    python3 citation_seed.py apply --write --ids ... --out-dir $CITSEED_OUT/r2_gpt
    ./run_annotator.sh $CITSEED_ANNOT 8126          # viewer on :8126
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXP = HERE.parent                    # the experiment root; data/ lives there
TRIAGE = EXP.parent / "experiments_09022026" / "triage"
CL_LINK = re.compile(
    r'<a href="/opinion/(\d+)/[^"]*"(?:\s+aria-description="Citation for case: ([^"]*)")?[^>]*>(.*?)</a>', re.S)
TAG = re.compile(r"<[^>]+>")
GROUP = {"scotus": "scotus", "fed_appellate": "circuit", "fed_district": "circuit",
         "state_supreme": "state_high", "state_appellate": "state_high"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", default=str(EXP / "data" / "cl20k_r2" / "blocks"))
    ap.add_argument("--sample-dir", default=str(EXP / "data" / "r2_sample"))
    ap.add_argument("--out", default=str(EXP / "data" / "annotator_r2"))
    ap.add_argument("--limit", type=int, help="only the first N clusters (pilot)")
    ap.add_argument("--pools", nargs="*", default=["selected", "topup"])
    a = ap.parse_args()
    sample = Path(a.sample_dir).resolve()
    a.out = str(Path(a.out).resolve())
    (sample / "opinion_html").mkdir(parents=True, exist_ok=True)
    meta, auth, ids = [], [], []
    today = date.today().isoformat()
    # pool membership comes from sample_metadata.csv: the sampler relabels
    # overflow rows to "topup" only there, after the blocks were written
    with open(Path(a.blocks).parent / "sample_metadata.csv", encoding="utf-8") as fh:
        pool_of = {r["cluster_id"]: r["pool"] for r in csv.DictReader(fh)}
    for blk in sorted(Path(a.blocks).glob("*.jsonl.gz")):
        with gzip.open(blk, "rt", encoding="utf-8") as f:
            for ln in f:
                r = json.loads(ln)
                r["pool"] = pool_of.get(str(r["cluster_id"]), r["pool"])
                if r["pool"] not in a.pools:
                    continue
                cid = str(r["cluster_id"])
                if a.limit and len(ids) >= a.limit:
                    break
                ids.append(cid)
                payload = {"cluster_id": r["cluster_id"], "origin": "clreplica", "fetched": r.get("fetched", today)[:10],
                           "opinions": [{"id": o["id"], "type": o["type"], "source": "html_with_citations", "html": o["html"]}
                                        for o in r["opinions"]]}
                with open(sample / "opinion_html" / f"{cid}.json", "w", encoding="utf-8") as out:
                    json.dump(payload, out, ensure_ascii=False)
                cited = {}
                for o in r["opinions"]:
                    for m in CL_LINK.finditer(o["html"]):
                        ccid, name, text = m.group(1), m.group(2) or "", TAG.sub("", m.group(3)).strip()
                        if ccid == cid:
                            continue
                        e = cited.setdefault(ccid, {"name": name, "cites": []})
                        if name and not e["name"]:
                            e["name"] = name
                        if text and text not in e["cites"]:
                            e["cites"].append(text)
                meta.append({"cluster_id": cid, "court": r["court"], "court_group": GROUP.get(r["court_group"], "state_high"),
                             "case_name": r["case_name"], "year": r["year"], "text_length": r["text_length"],
                             "selection": r["kind"], "n_cited_clusters": len(cited),
                             "cl_court_group": r["court_group"], "decade": r["decade"], "length_bin": r["length_bin"],
                             "n_strong_near": r.get("n_strong_near", 0), "strong_terms": r.get("strong_terms", "")})
                for ccid, e in cited.items():
                    auth.append({"citing_cluster_id": cid, "cited_cluster_id": ccid, "cited_case_name": e["name"],
                                 "cited_case_citations": "; ".join(e["cites"][:6])})
        if a.limit and len(ids) >= a.limit:
            break
    with open(sample / "sample_metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(meta[0].keys()))
        w.writeheader()
        w.writerows(meta)
    with open(sample / "authority_metadata.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["citing_cluster_id", "cited_cluster_id", "cited_case_name", "cited_case_citations"])
        w.writeheader()
        w.writerows(auth)
    print(f"sample dir {sample}: {len(ids)} clusters, {len(auth)} authority rows")
    subprocess.run([sys.executable, str(TRIAGE / "make_annotator_data.py"), "--sample-dir", str(sample), "--out", a.out],
                   check=True, cwd=str(TRIAGE))
    out = Path(a.out)
    (out / "overrides" / "citation_changes.csv").touch()
    if os.path.getsize(out / "overrides" / "citation_changes.csv") == 0:
        (out / "overrides" / "citation_changes.csv").write_text("citing_cluster_id,ref,action,by,at\n")
    (out / "data" / "revised_html").mkdir(exist_ok=True)
    (out / "data" / "seed_reviews").mkdir(exist_ok=True)
    (out / "ids.txt").write_text("\n".join(ids) + "\n")
    print(f"annotator root {out}: ids.txt ({len(ids)}), citation_changes.csv, revised_html/, seed_reviews/ ready")


if __name__ == "__main__":
    main()
