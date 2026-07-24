"""Pure I/O over a dataset tree. No interpretation here — decoding lives in
pipeline.engines, placement in pipeline.core.layout."""

from __future__ import annotations

import json

from pipeline.core.config import Dataset
from pipeline.core.pages import Page


def container_dets(ds: Dataset, page_id: str) -> list[dict]:
    """Raw container-YOLO detections: [{label, bbox, confidence}]."""
    f = ds.engine_dir("container_yolo") / f"{page_id}.json"
    if not f.exists():
        return []
    return json.loads(f.read_text(encoding="utf-8"))


def redaction_rects(ds: Dataset, page: Page) -> list[dict]:
    """Redaction rects as [{bbox, fill, type}] in 1700x2200 pixel space,
    from the per-volume redaction_rects.json. Only consulted when a page
    render is missing — the bundled datasets ship page_png/ pre-rendered,
    which is exactly what the cached engine outputs saw."""
    f = page.vol_dir / "redaction_rects.json"
    if not f.exists():
        return []
    for entry in json.loads(f.read_text(encoding="utf-8")):
        if entry.get("page_index") == page.page_index:
            return [
                {
                    "bbox": [r["x0"], r["y0"], r["x1"], r["y1"]],
                    "type": r.get("type", ""),
                    "fill": r.get("fill", "black"),
                }
                for r in entry.get("rects", [])
            ]
    return []
