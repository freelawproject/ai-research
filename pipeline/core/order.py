"""Reading order from bbox geometry alone — no container model.

The container detector is trained on redacted pages and collapses on
unredacted ones (whole-page `image` boxes, no columns), so on those sets
its columns cannot drive placement. Everything here is derived from the
boxes themselves.

A page is read as three bands: the running heads above the text block,
the body, and anything below the body's foot. The body is split into
columns at a boundary inferred from the boxes' LEFT EDGES — a
two-column reporter page puts every x0 into one of two tight clusters
hundreds of pixels apart, which is far more robust than hunting for the
~20px of real whitespace in the gutter (the centered running head
straddles it, and the columns' text runs right up to it). A box that
straddles the boundary is full-width: it separates the columns above it
from the columns below, so ordering restarts underneath.

Measured against dots' own reading order over 897 unredacted pages:
0.47% pairwise inversion, 77% of pages ordered identically. A plain
(y, x) sweep over the same pages inverts 20.37%.
"""

from __future__ import annotations

from pipeline.core.config import RENDER_H, RENDER_W

# Bands. A box entirely above TOP_BAND is a running head; one starting
# below BOTTOM_BAND is a footer. Both span the page and belong to no
# column.
TOP_BAND = 0.085 * RENDER_H
BOTTOM_BAND = 0.95 * RENDER_H
# Left-edge gap that means "a second column starts here".
COLUMN_GAP = 200.0
# The boundary sits slightly left of the right column's first edge.
EDGE_PAD = 20.0
# How far a box must reach past the boundary on BOTH sides to count as
# full-width rather than as a column member that overshoots.
STRADDLE_L = 40.0
STRADDLE_R = 60.0
# Boxes within this many pixels of each other vertically are on one
# line and read left-to-right (the two running heads sit level).
LINE_BAND = 20.0


def line_sort(boxes: list[dict]) -> list[dict]:
    """Top-to-bottom, left-to-right within a line band. Used for the
    head/foot bands and for the members of one aligned group, where
    there are no columns to reason about."""
    return sorted(
        boxes,
        key=lambda b: (round(b["bbox"][1] / LINE_BAND), b["bbox"][0]),
    )


def _split_bands(
    boxes: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    head, foot, body = [], [], []
    for b in boxes:
        if b["bbox"][3] < TOP_BAND:
            head.append(b)
        elif b["bbox"][1] > BOTTOM_BAND:
            foot.append(b)
        else:
            body.append(b)
    return head, body, foot


def column_boundary(boxes: list[dict]) -> float | None:
    """The x separating the two columns, or None for a single column.

    Only body boxes vote: running heads and footers straddle the gutter
    and would hide it.
    """
    _, body, _ = _split_bands(boxes)
    edges = sorted(b["bbox"][0] for b in body)
    if len(edges) < 4:
        return None
    gap, right_edge = 0.0, None
    for left, right in zip(edges, edges[1:]):
        if right - left > gap:
            gap, right_edge = right - left, right
    if gap < COLUMN_GAP or right_edge is None:
        return None
    boundary = right_edge - EDGE_PAD
    if not 0.25 * RENDER_W < boundary < 0.85 * RENDER_W:
        return None
    if sum(1 for e in edges if e < boundary) < 2:
        return None
    if sum(1 for e in edges if e >= boundary) < 2:
        return None
    return boundary


def _straddles(bbox: list[float], boundary: float) -> bool:
    return bbox[0] < boundary - STRADDLE_L and bbox[2] > boundary + STRADDLE_R


def _side(bbox: list[float], boundary: float) -> str:
    return "L" if (bbox[0] + bbox[2]) / 2 < boundary else "R"


def place(boxes: list[dict]) -> list[dict]:
    """Boxes in reading order, each stamped with `band` (head/body/foot)
    and `column` (L/R, or None when the page has one column or the box
    spans both). Input dicts are not mutated."""
    head, body, foot = _split_bands(boxes)
    boundary = column_boundary(boxes)

    ordered: list[dict] = []
    if boundary is None:
        ordered = [
            {**b, "band": "body", "column": None}
            for b in sorted(body, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        ]
    else:
        seps = sorted(
            (b for b in body if _straddles(b["bbox"], boundary)),
            key=lambda b: b["bbox"][1],
        )
        rest = [b for b in body if not _straddles(b["bbox"], boundary)]
        top = -1.0
        for sep in [*seps, None]:
            cut = sep["bbox"][1] if sep is not None else float(RENDER_H) + 1
            segment = [b for b in rest if top <= b["bbox"][1] < cut]
            segment.sort(
                key=lambda b: (
                    _side(b["bbox"], boundary),
                    b["bbox"][1],
                    b["bbox"][0],
                )
            )
            ordered += [
                {**b, "band": "body", "column": _side(b["bbox"], boundary)}
                for b in segment
            ]
            if sep is not None:
                ordered.append({**sep, "band": "body", "column": None})
            top = cut

    return (
        [{**b, "band": "head", "column": None} for b in line_sort(head)]
        + ordered
        + [{**b, "band": "foot", "column": None} for b in line_sort(foot)]
    )
