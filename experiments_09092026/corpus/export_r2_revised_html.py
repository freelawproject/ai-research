"""Export the GPT-seeded round-2 labels as revised HTML, the same format the
triage annotator exports for dev/test/train (`<section data-opinion-type>` /
`<cite group cited_ref change>` / `<noncite>`), so build_dataset.py can read
them as gold-format records.

Runs the viewer's `revised_citation_html` in-process against the round-2 root
(no server needed). Env must point the viewer at the root BEFORE app is
imported — use the wrapper:

    bash corpus/export_r2_revised_html.sh              # all seeded clusters
    bash corpus/export_r2_revised_html.sh --limit 20   # smoke

Clusters are exported only if their grouping override carries
`llm_citation_pass` (GPT edits applied). Clusters GPT failed on or refused
(listed in data/citation_seed_r2/batches/*.errors.json) are excluded and
written to data/annotator_r2/ids_failed.txt; the exported set goes to
data/annotator_r2/ids_seeded.txt (the ids build_dataset.py should train on).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
EXP = os.path.dirname(HERE)          # the experiment root; data/ lives there
# the citator-benchmark checkout, whose viewer code does the rendering;
# sibling of the ai-research checkout by default, CITATOR_BENCH to override
BENCH = os.environ.get("CITATOR_BENCH",
                       os.path.join(EXP, "..", "..", "citator-benchmark"))
ROOT = os.path.join(EXP, "data", "annotator_r2")
SEED = os.path.join(EXP, "data", "citation_seed_r2")
sys.path.insert(0, BENCH)
import app  # noqa: E402  (reads CITATOR_BENCH_* env at import; set by the .sh wrapper)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="rewrite files that already exist")
    a = ap.parse_args()
    if os.path.realpath(app.DATA_DIR) != os.path.realpath(os.path.join(a.root, "data")):
        sys.exit(f"viewer DATA_DIR is {app.DATA_DIR}, not {a.root}/data — run via corpus/export_r2_revised_html.sh")
    with open(os.path.join(a.root, "ids.txt"), encoding="utf-8") as fh:
        ids = fh.read().split()
    failed = {}
    for f in glob.glob(os.path.join(SEED, "batches", "*.errors.json")):
        with open(f, encoding="utf-8") as fh:
            failed.update(json.load(fh))
    out_dir = os.path.join(app.DATA_DIR, "revised_html")
    os.makedirs(out_dir, exist_ok=True)
    seeded, unseeded, written, skipped, missing = [], [], 0, 0, 0
    t0 = time.time()
    for i, cid in enumerate(ids):
        if cid in failed:
            continue
        ov_path = os.path.join(app.DATA_DIR, "grouping_overrides", f"{cid}.json")
        seeded = False
        if os.path.exists(ov_path):
            with open(ov_path, encoding="utf-8") as fh:
                seeded = bool(json.load(fh).get("llm_citation_pass"))
        if not seeded:
            unseeded.append(cid)
            continue
        seeded.append(cid)
        if a.limit and written + skipped >= a.limit:
            continue
        path = os.path.join(out_dir, f"{cid}.html")
        if os.path.exists(path) and os.path.getsize(path) > 0 and not a.force:
            skipped += 1
            continue
        rev = app.revised_citation_html(int(cid))
        if rev is None:
            missing += 1
            continue
        with open(path, "w", encoding="utf-8") as f:
            f.write(app._revised_html_body(rev))
        written += 1
        if written % 500 == 0:
            print(f"  {written} written ({time.time() - t0:.0f}s)", flush=True)
    with open(os.path.join(a.root, "ids_seeded.txt"), "w") as f:
        f.write("\n".join(seeded) + "\n")
    with open(os.path.join(a.root, "ids_failed.txt"), "w") as f:
        f.write("\n".join(f"{c}\t{failed[c]}" for c in ids if c in failed) + "\n")
    print(f"{len(ids)} ids: {len(seeded)} seeded, {len(failed)} failed (excluded), {len(unseeded)} without llm pass")
    print(f"revised_html: {written} written, {skipped} already present, {missing} not cached -> {out_dir}")
    print(f"ids_seeded.txt / ids_failed.txt written under {a.root}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
