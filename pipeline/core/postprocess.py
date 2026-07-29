"""Container-YOLO detection postprocessing — the same rules the model's
training labels were built with, applied to every page's detections before
they become the layout. (runpod/kits/container_yolo/postprocess.py is a
deliberate standalone twin — kits import nothing from the pipeline — so
change the rules in both places together.)

  0. DEDUPE: same-class boxes overlapping substantially (IoU>0.5 OR the
     smaller box >=50% covered) keep only the higher-confidence one.
     (Any-overlap would delete a legit L/R column pair at the gutter.)
  1. ONE page_number per page: highest confidence wins, overlap or not.
  2. body/column boxes END BEFORE the footnote block: boxes overlapping the
     footnote_block vertically are clipped to its top; a box left shorter
     than 40px lived inside the footnote region and is dropped.
  3. footnote_block WIDTH >= the combined body/column x-extent: widened to
     match when narrower, never narrowed.
"""

from __future__ import annotations

from collections import Counter

from pipeline.core import geometry

BODYISH = ("column", "body")
MIN_COL_H = 40
NMS_IOU = 0.5
NMS_COVER = 0.5


def _dup(a: list[float], b: list[float]) -> bool:
    i = geometry.inter(a, b)
    if i <= 0:
        return False
    u = geometry.area(a) + geometry.area(b) - i
    return (u > 0 and i / u > NMS_IOU) or (
        i / min(geometry.area(a), geometry.area(b)) > NMS_COVER
    )


def postprocess_dets(
    dets: list[dict], stats: Counter | None = None
) -> list[dict]:
    """Apply all rules to one page's detections; returns the cleaned list."""
    if stats is None:
        stats = Counter()

    # 0) dedupe (same class, substantial overlap, keep highest confidence)
    kept: list[dict] = []
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

    # anchor footnote_block = highest-confidence one
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

    # 3) footnote_block at least as wide as the body/column extent
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
                        kept[i] = {
                            **d,
                            "bbox": [round(v, 1) for v in nb],
                        }
                        break

    return kept
