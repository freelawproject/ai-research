"""Mistral OCR loader + decode.

On-disk raw shape: [{type, bbox, text}] per page — whole-page OCR with block
bboxes (include_blocks); text is Markdown with styling; bboxes live in the
canonical 1700x2200 space. A page may instead hold the whole response
object ({markdown, blocks}) — the block list inside is the same shape.
"""

from __future__ import annotations

import json

from pipeline.core.config import Dataset
from pipeline.core.markup import mistral_md_to_html

ENGINE = "mistral"


def load(ds: Dataset, page_id: str) -> list[dict] | None:
    f = ds.engine_dir(ENGINE) / f"{page_id}.json"
    if not f.exists():
        return None
    raw = json.loads(f.read_text(encoding="utf-8") or "[]")
    if isinstance(raw, dict):  # whole-response shape: {markdown, blocks}
        return list(raw.get("blocks", []))
    return raw


def blocks(raw: list[dict]) -> list[dict]:
    """The decoded block list: [{id, type, label, bbox, text, styled}].
    `styled` = the Markdown styling converted to sanitized HTML;
    `label` mirrors `type` so every engine's blocks read uniformly."""
    return [
        {
            "id": i,
            "type": b.get("type", ""),
            "label": b.get("type", ""),
            "bbox": b.get("bbox"),
            "text": b.get("text", ""),
            "styled": mistral_md_to_html(b.get("text", "")),
        }
        for i, b in enumerate(raw)
    ]
