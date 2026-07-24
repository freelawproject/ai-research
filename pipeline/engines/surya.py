"""Surya — supplemental engine of the surya_line, surya_block and
three_way routes. Loader + decode.

Both variants share ONE on-disk shape: {"stem", "mode", "text",
"regions": [{order, label, raw_label, bbox, confidence, html, text}],
"raw"} — only the region granularity differs. `variant="line"` = detection
lines -> per-line OCR (fine geometry; bleed-through filtered against dots
blocks at reconstruction). `variant="block"` = full-page layout blocks
(whole-page context). One decoder, regions(), serves both; run.py keys the
result "lines" or "blocks" by unit. Bboxes live in the canonical 1700x2200
space.
"""

from __future__ import annotations

import json

from pipeline.core.config import Dataset
from pipeline.core.markup import html_to_html

ENGINE = "surya"


def load(
    ds: Dataset, page_id: str, variant: str = "line"
) -> dict | None:
    f = ds.engine_dir(ENGINE) / variant / f"{page_id}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8") or "{}") or None


def regions(raw: dict) -> list[dict]:
    """The decoded region list (lines OR blocks, depending on which
    variant was loaded): [{id, order, label, bbox, confidence, text, html,
    styled}]. `html` is the model's verbatim output; `styled` is that HTML
    sanitized to the shared presentation vocabulary (i/b -> em/strong, sup
    kept, structural tags unwrapped)."""
    out = []
    for i, r in enumerate(raw.get("regions", [])):
        fragment = r.get("html", "") or r.get("text", "")
        out.append(
            {
                "id": i,
                "order": r.get("order", i),
                "label": r.get("label", ""),
                "bbox": r.get("bbox"),
                "confidence": r.get("confidence"),
                "text": r.get("text", ""),
                "html": r.get("html", ""),
                "styled": html_to_html(fragment),
            }
        )
    return out
