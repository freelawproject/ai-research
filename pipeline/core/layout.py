"""Layout: container-YOLO detections -> the container set text
bboxes are placed into. Raw detections pass through postprocess_dets (the
same rules the model's training consumed), then columns get two robustness
rules for out-of-domain pages:

1. OVERLAP DEDUP — a column (almost) entirely covered by another is the SAME
   column detected twice; keep the larger (a doubly-detected single column
   must not become a bogus L/R pair).
2. RELATIVE L/R — survivors sort left->right; the two-column case is labeled
   L/R by POSITION, not by page midline (which mislabels two columns that
   both sit on one side of the page).
"""

from __future__ import annotations

from collections import Counter

from pipeline.core import geometry, readers
from pipeline.core.config import RENDER_W, Dataset
from pipeline.core.postprocess import postprocess_dets

# Classes that act as placement containers, in containment priority
# (first match wins). image/table are display/flow blocks.
PRIORITY = (
    "page_number",
    "footnote_block",
    "blockquote",
    "heading",
    "caption",
    "column",
)


def _side(bbox: list[float]) -> str:
    return "L" if (bbox[0] + bbox[2]) / 2 < RENDER_W / 2 else "R"


def _columns(col_dets: list[dict]) -> list[dict]:
    cols = [
        {"bbox": d["bbox"], "confidence": d.get("confidence")}
        for d in col_dets
    ]
    cols.sort(key=lambda c: geometry.area(c["bbox"]), reverse=True)
    kept: list[dict] = []
    for c in cols:
        if any(geometry.cover_frac(c["bbox"], k["bbox"]) >= 0.8 for k in kept):
            continue  # >=80% inside a larger kept column: same column twice
        kept.append(c)
    kept.sort(key=lambda c: c["bbox"][0])  # left -> right
    if len(kept) == 2:
        kept[0]["side"], kept[1]["side"] = "L", "R"
    else:
        for c in kept:
            c["side"] = _side(c["bbox"])
    return kept


def containers_for(ds: Dataset, page_id: str) -> dict:
    """{"raw", "post", "stats", "columns", "by_label"} for one page."""
    raw = readers.container_dets(ds, page_id)
    stats: Counter = Counter()
    post = postprocess_dets(raw, stats)
    by_label: dict[str, list[dict]] = {}
    for d in post:
        by_label.setdefault(d["label"], []).append(d)
    return {
        "raw": raw,
        "post": post,
        "stats": dict(stats),
        "columns": _columns(by_label.get("column", [])),
        "by_label": by_label,
    }


def _contains(region: list[float], bb: list[float], pad: float = 6.0) -> bool:
    cx, cy = geometry.center(bb)
    x0, y0, x1, y1 = region
    return x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad


def assign(containers: dict, bb: list[float]) -> tuple[str | None, str | None]:
    """(container_label, column_side) for a text bbox, by center-in-container
    with a 6px pad, in PRIORITY order. A bbox whose center is outside EVERY
    column keeps side=None — reading_order then places it by (y, x) position
    instead of forcing it into a column (a no-column page reads top->bottom
    rather than being split into pseudo-columns)."""
    hit = None
    for label in PRIORITY:
        boxes = containers["by_label"].get(label, [])
        if any(_contains(d["bbox"], bb) for d in boxes):
            hit = label
            break
    side = None
    cx = geometry.center(bb)[0]
    for c in containers["columns"]:
        if c["bbox"][0] - 6 <= cx <= c["bbox"][2] + 6:
            side = c["side"]
            break
    return hit, side


def reading_order(blocks: list[dict]) -> list[dict]:
    """Shared reconstruction ordering: page number first, then body by
    (column L->R, y, x), then the footnote band by (column, y, x). Blocks in
    no column (column=None) fall after the columns, ordered by (y, x). Each
    block carries a `band` in {page_number, body, footnote} and a `column`."""

    def key(b: dict) -> tuple[int, float, float]:
        col = (
            0 if b.get("column") == "L" else 1 if b.get("column") == "R" else 2
        )
        bb = b.get("bbox") or [0.0, 0.0, 0.0, 0.0]
        return (col, bb[1], bb[0])

    pn = [b for b in blocks if b.get("band") == "page_number"]
    body = sorted((b for b in blocks if b.get("band") == "body"), key=key)
    foot = sorted((b for b in blocks if b.get("band") == "footnote"), key=key)
    return pn + body + foot


def band_of(role: str) -> str:
    """The reading-order band a container-derived role belongs to."""
    if role == "page_number":
        return "page_number"
    if role == "footnote":
        return "footnote"
    return "body"
