"""Mistral OCR — supplemental engine of the `mistral` route. Loader + decode.

On-disk raw shape: [{type, bbox, text}] per page — whole-page OCR with block
bboxes (include_blocks); text is Markdown with styling; bboxes live in the
canonical 1700x2200 space.
"""

from __future__ import annotations

import json

from pipeline.core.config import Dataset

ENGINE = "mistral"


def load(ds: Dataset, page_id: str) -> list[dict] | None:
    f = ds.engine_dir(ENGINE) / f"{page_id}.json"
    if not f.exists():
        return None
    return json.loads(f.read_text(encoding="utf-8") or "[]")


def blocks(raw: list[dict]) -> list[dict]:
    """The decoded block list: [{id, type, bbox, text}]."""
    return [
        {
            "id": i,
            "type": b.get("type", ""),
            "bbox": b.get("bbox"),
            "text": b.get("text", ""),
        }
        for i, b in enumerate(raw)
    ]
