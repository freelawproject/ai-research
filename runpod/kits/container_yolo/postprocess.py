"""Post-process container-model predictions — the same rules the model's
training labels were built with. Standalone, stdlib only, safe to run on
the pod right after inference (kits import nothing from the pipeline, so
this is a deliberate twin of pipeline/core/postprocess.py — change the
rules in both places together):

    python postprocess.py --in preds --out preds_post

Rules, in order, per page:
  0. DEDUPE: same-class suppression — two boxes of the same type overlapping
     substantially (IoU>0.5 OR the smaller box ≥50% covered) keep only the
     higher-confidence one. (Any-overlap would delete a legit L/R column at
     the gutter, so 'substantial' it is.)
  1. ONE page_number per page: keep the highest-confidence one (even when
     the duplicates don't overlap).
  2. body/column boxes END BEFORE the footnote block: any body/column box
     overlapping the footnote_block vertically is clipped to y2 = fb.y1;
     a box left shorter than 40px (i.e. it lived inside the footnote region)
     is dropped. The highest-confidence footnote_block anchors the rule.
  3. footnote_block WIDTH >= combined body/column x-extent: widen the fb to
     match when narrower; if the fb is already wider, nothing is adjusted.

Reads/writes the per-page detection JSONs ([{label, bbox, confidence}]).
Also importable: postprocess_dets(dets) -> cleaned dets (infer.py applies
it to every page unless --raw).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

BODYISH = ("column", "body")
MIN_COL_H = 40
NMS_IOU = 0.5
NMS_COVER = 0.5


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _dup(a, b):
    i = _inter(a, b)
    if i <= 0:
        return False
    u = _area(a) + _area(b) - i
    return (u > 0 and i / u > NMS_IOU) or i / min(
        _area(a), _area(b)
    ) > NMS_COVER


def postprocess_dets(dets, stats: Counter | None = None):
    """Apply all rules to one page's detections; returns the cleaned list."""
    if stats is None:
        stats = Counter()

    # 0) dedupe (same-class, substantial overlap, keep highest confidence)
    kept = []
    for d in sorted(dets, key=lambda x: -(x.get("confidence") or 0)):
        if any(
            k["label"] == d["label"] and _dup(k["bbox"], d["bbox"])
            for k in kept
        ):
            stats["deduped"] += 1
            continue
        kept.append(d)

    # 1) single page_number (highest confidence wins, overlap or not)
    pns = [d for d in kept if d["label"] == "page_number"]
    if len(pns) > 1:
        best = max(pns, key=lambda d: d.get("confidence") or 0)
        stats["extra_page_numbers"] += len(pns) - 1
        kept = [d for d in kept if d["label"] != "page_number"] + [best]

    # anchor footnote_block = highest confidence one (others left untouched)
    fbs = [d for d in kept if d["label"] == "footnote_block"]
    fb = max(fbs, key=lambda d: d.get("confidence") or 0) if fbs else None

    # 2) body/column boxes end before the footnote block
    if fb is not None:
        fb_top = fb["bbox"][1]
        out = []
        for d in kept:
            if d["label"] in BODYISH and d["bbox"][3] > fb_top:
                nb = [d["bbox"][0], d["bbox"][1], d["bbox"][2], fb_top]
                if nb[3] - nb[1] < MIN_COL_H:
                    stats["cols_dropped_in_fb"] += 1
                    continue
                d = {**d, "bbox": [round(v, 1) for v in nb]}
                stats["cols_clipped"] += 1
            out.append(d)
        kept = out

    # 3) footnote_block at least as wide as the combined body/column extent
    if fb is not None:
        cols = [d["bbox"] for d in kept if d["label"] in BODYISH]
        if cols:
            x0 = min(c[0] for c in cols)
            x1 = max(c[2] for c in cols)
            b = fb["bbox"]
            if b[0] > x0 or b[2] < x1:
                nb = [min(b[0], x0), b[1], max(b[2], x1), b[3]]
                stats["fb_widened"] += 1
                for i, d in enumerate(kept):
                    if d is fb or (
                        d["label"] == "footnote_block" and d["bbox"] == b
                    ):
                        kept[i] = {**d, "bbox": [round(v, 1) for v in nb]}
                        break

    return kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--in",
        dest="src",
        required=True,
        help="dir of raw per-page detection JSONs",
    )
    ap.add_argument(
        "--out", dest="dst", required=True, help="output dir for cleaned JSONs"
    )
    args = ap.parse_args()
    src, dst = Path(args.src), Path(args.dst)
    dst.mkdir(parents=True, exist_ok=True)
    stats: Counter = Counter()
    n = 0
    for f in sorted(src.glob("*.json")):
        dets = postprocess_dets(json.loads(f.read_text()), stats)
        (dst / f.name).write_text(json.dumps(dets))
        n += 1
        if n % 5000 == 0:
            print(f"  {n} pages …")
    print(f"{n} pages → {dst}")
    print("changes:", dict(stats) or "none")


if __name__ == "__main__":
    main()
