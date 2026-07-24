"""Gemini — supplemental engine of the `gemini` route. Loader + decode.

Gemini outputs are generated UPSTREAM (redacted PDF with an embedded text
layer + prompt & clues) and consumed here as input data: one tagged XML
document per page, no bboxes. A missing or empty file is a refusal.

Decode walks the XML in document order and emits one block per block-level
tag; detail tags (citation, em, footnotemark, ...) stay inline in the block
text and are preserved verbatim in the raw XML for later stages.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from pipeline.core.config import Dataset

ENGINE = "gemini"

BLOCK_TAGS = frozenset(
    {
        "pagenumber",
        "parallelpagenumber",
        "p",
        "blockquote",
        "heading",
        "caption",
        "footnote",
        "image",
        "table",
    }
)


def load(ds: Dataset, page_id: str) -> str | None:
    """Raw tagged XML, or None if missing (refusal)."""
    f = ds.engine_dir(ENGINE) / f"{page_id}.xml"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace")


def is_refusal(raw: str | None) -> bool:
    return raw is None or not raw.strip()


def _walk(el: ET.Element, out: list[dict]) -> None:
    for child in el:
        if child.tag in BLOCK_TAGS:
            text = "".join(child.itertext()).strip()
            out.append(
                {
                    "id": len(out),
                    "tag": child.tag,
                    "attrs": dict(child.attrib),
                    "text": text,
                }
            )
        else:
            _walk(child, out)


def decode(raw: str) -> dict:
    """{"blocks": [{id, tag, attrs, text}], "parse_error": str | None}."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {"blocks": [], "parse_error": str(exc)}
    blocks: list[dict] = []
    _walk(root, blocks)
    return {"blocks": blocks, "parse_error": None}
