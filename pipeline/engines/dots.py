"""dots.mocr — the MAIN OCR engine in every route. Loader + decode.

On-disk raw shape: {"text": <page markdown>, "regions": [{order, label,
bbox, text}]}. Region text is Markdown for Text/Title regions; bboxes live
in the canonical 1700x2200 space.
"""

from __future__ import annotations

import json

from pipeline.core.config import Dataset

ENGINE = "dots"


def load(ds: Dataset, page_id: str) -> dict | None:
    f = ds.engine_dir(ENGINE) / f"{page_id}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8") or "{}") or None


def blocks(raw: dict) -> list[dict]:
    """The decoded block list: [{id, order, label, bbox, text}]."""
    out = []
    for i, r in enumerate(raw.get("regions", [])):
        out.append(
            {
                "id": i,
                "order": r.get("order", i),
                "label": r.get("label", ""),
                "bbox": r.get("bbox"),
                "text": r.get("text", ""),
            }
        )
    return out
