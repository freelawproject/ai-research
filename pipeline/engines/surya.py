"""Surya — supplemental engine of the `surya` route. Loader + decode.

The route consumes the LINE variant (detection lines -> per-line OCR):
{"stem", "mode", "text", "regions": [{order, label, raw_label, bbox,
confidence, html, text}], "raw"}. The block variant (full-page layout
blocks, same shape) ships alongside as reference. Line bboxes live in the
canonical 1700x2200 space. Lines not contained in any dots block are
bleed-through hallucinations — dropped at reconstruction (stage 6).
"""

from __future__ import annotations

import json

from pipeline.core.config import Dataset

ENGINE = "surya"


def load(
    ds: Dataset, page_id: str, variant: str = "line"
) -> dict | None:
    f = ds.engine_dir(ENGINE) / variant / f"{page_id}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8") or "{}") or None


def lines(raw: dict) -> list[dict]:
    """The decoded line list: [{id, order, label, bbox, confidence, text}].
    HTML markup in line text is kept verbatim for the decode layer (em/sup
    are meaningful; other tags get dropped during normalization)."""
    out = []
    for i, r in enumerate(raw.get("regions", [])):
        out.append(
            {
                "id": i,
                "order": r.get("order", i),
                "label": r.get("label", ""),
                "bbox": r.get("bbox"),
                "confidence": r.get("confidence"),
                "text": r.get("text", ""),
                "html": r.get("html", ""),
            }
        )
    return out
