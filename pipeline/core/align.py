"""Align each engine's regions to the same pieces of the page.

The engines do not agree on how a page divides into blocks: one emits a
single header box where another emits three, so a naive 1:1 bbox match
throws most of the page away. Two regions from DIFFERENT engines are
linked when their intersection covers at least OVERLAP of the SMALLER
one — containment, not IoU, because a small box inside a big one is
obviously the same content but scores badly on IoU. Groups are the
connected components of those links, so any N:M split resolves in one
pass.

Alignment is GEOMETRY-ONLY: text never decides what merges. Two guards
make that safe. Page-scale regions (>= MAX_AREA of the page) are held
out of the link graph — a whole-page picture box overlaps everything
and would chain the page into one useless group. And every group
reports the IoU of the engines' MERGED boxes, so a bad ALIGNMENT can be
told apart from a real OCR disagreement: linking is deliberately
permissive, and afterwards the merged boxes should still occupy nearly
the same rectangle.
"""

from __future__ import annotations

import html
import re
from itertools import combinations

from pipeline.core.config import RENDER_H, RENDER_W
from pipeline.core.order import line_sort

PAGE_AREA = float(RENDER_W * RENDER_H)
# Minimum intersection, as a fraction of the smaller box, to link.
OVERLAP = 0.5
# Regions at least this fraction of the page do not link.
MAX_AREA = 0.5

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
# Mistral writes "![img-0.jpeg](img-0.jpeg)" as a block's whole text
# where the others emit an empty figure box: a placeholder, not content.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def plain(fragment: str | None) -> str:
    """Markup stripped and whitespace collapsed — the block's content
    with none of its styling."""
    if not fragment:
        return ""
    stripped = _TAG.sub(" ", _MD_IMAGE.sub(" ", fragment))
    return _WS.sub(" ", html.unescape(stripped)).strip()


def _area(bbox: list[float]) -> float:
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _intersection(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def contained(a: list[float], b: list[float]) -> float:
    """Intersection as a fraction of the SMALLER box's area."""
    smaller = min(_area(a), _area(b))
    return _intersection(a, b) / smaller if smaller > 0 else 0.0


def iou(a: list[float], b: list[float]) -> float:
    """Plain IoU — used on MERGED boxes to score the alignment."""
    inter = _intersection(a, b)
    union = _area(a) + _area(b) - inter
    return inter / union if union > 0 else 0.0


def _normalized(bbox: list[float] | None) -> list[float] | None:
    if not bbox or len(bbox) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in bbox)
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


class _Union:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def join(self, i: int, j: int) -> None:
        a, b = self.find(i), self.find(j)
        if a != b:
            self.parent[a] = b


def _flatten(per_engine: dict[str, list[dict]]) -> list[dict]:
    """Every engine's bbox-carrying blocks in one list."""
    flat: list[dict] = []
    for engine, blocks in per_engine.items():
        for block in blocks:
            bbox = _normalized(block.get("bbox"))
            if bbox is None:
                continue
            flat.append(
                {
                    "engine": engine,
                    "id": block.get("id"),
                    "bbox": bbox,
                    "area": _area(bbox),
                    "label": block.get("label") or block.get("type") or "",
                    "text": block.get("text", ""),
                    "html": block.get("styled", block.get("text", "")),
                }
            )
    return flat


def _merge(members: list[dict]) -> dict:
    """One engine's members of a group, merged into a single box with
    its text concatenated in reading order."""
    ordered = line_sort(members)
    return {
        "n_boxes": len(ordered),
        "ids": [m["id"] for m in ordered],
        "bbox": [
            min(m["bbox"][0] for m in ordered),
            min(m["bbox"][1] for m in ordered),
            max(m["bbox"][2] for m in ordered),
            max(m["bbox"][3] for m in ordered),
        ],
        "labels": [m["label"] for m in ordered],
        "text": " ".join(t for t in (plain(m["text"]) for m in ordered) if t),
        "html": "\n".join(m["html"] for m in ordered if m["html"]),
    }


def align_page(
    per_engine: dict[str, list[dict]],
    overlap: float = OVERLAP,
    max_area: float = MAX_AREA,
) -> list[dict]:
    """Aligned groups, unordered. Each is
    {engines: {name: merged}, bbox, present, page_scale, alignment_iou}
    where `bbox` is the union across every engine present and
    `alignment_iou` is the WORST pairwise IoU of the merged boxes
    (1.0 when only one engine is present)."""
    flat = _flatten(per_engine)
    cutoff = max_area * PAGE_AREA
    page_scale = {i for i, r in enumerate(flat) if r["area"] >= cutoff}

    union = _Union(len(flat))
    for i, j in combinations(range(len(flat)), 2):
        if i in page_scale or j in page_scale:
            continue
        if flat[i]["engine"] == flat[j]["engine"]:
            continue
        if contained(flat[i]["bbox"], flat[j]["bbox"]) >= overlap:
            union.join(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(len(flat)):
        buckets.setdefault(union.find(i), []).append(i)

    groups = []
    for indices in buckets.values():
        by_engine: dict[str, list[dict]] = {}
        for i in indices:
            by_engine.setdefault(flat[i]["engine"], []).append(flat[i])
        merged = {e: _merge(m) for e, m in by_engine.items()}
        boxes = [m["bbox"] for m in merged.values()]
        groups.append(
            {
                "engines": merged,
                "present": sorted(merged),
                "bbox": [
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ],
                "page_scale": any(i in page_scale for i in indices),
                "alignment_iou": round(
                    min(
                        (iou(a, b) for a, b in combinations(boxes, 2)),
                        default=1.0,
                    ),
                    3,
                ),
            }
        )
    return groups
