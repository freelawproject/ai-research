"""Server-side revised-html export for an annotator root, run inside the citator-benchmark
viewer's own uv environment so app.py's grouping logic is used verbatim:

  cd /Users/rachel/Desktop/flp/citator-benchmark && \
  CITATOR_BENCH_DATA_DIR=<root>/data CITATOR_BENCH_OVERRIDES_DIR=<root>/overrides \
  uv run python /Users/rachel/Desktop/flp/ai-research/experiments_09112026/export_revised_html.py --ids ...

Writes <root>/data/revised_html/{cid}.html — the corrected grouping as
`<section data-opinion-type><cite group="N" cited_ref="…">…</cite>…</section>`.
(seed_citations.sh export wraps this.)
"""
import argparse
import os
import sys

sys.path.insert(0, "/Users/rachel/Desktop/flp/citator-benchmark")
sys.path.insert(0, "/Users/rachel/Desktop/flp/citator-benchmark/pipeline")
import app  # noqa: E402  (the viewer module; importing it builds no server)
from common import DATA_DIR  # noqa: E402  (citator-benchmark/pipeline/common.py)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="+", required=True)
    a = ap.parse_args()
    out_dir = os.path.join(DATA_DIR, "revised_html")
    os.makedirs(out_dir, exist_ok=True)
    n_ok, n_missing = 0, []
    for cid in a.ids:
        rev = app.revised_citation_html(int(cid))
        if rev is None:
            n_missing.append(cid)
            continue
        with open(os.path.join(out_dir, f"{cid}.html"), "w", encoding="utf-8") as f:
            f.write(app._revised_html_body(rev))
        n_ok += 1
    print(f"revised html written for {n_ok} clusters -> {out_dir}; missing payload: {len(n_missing)} {n_missing[:10]}")


if __name__ == "__main__":
    main()
