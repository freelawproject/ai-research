"""Gemini — supplemental engine of the `gemini` route. Loader + decode.

Gemini outputs are generated UPSTREAM (redacted PDF with an embedded text
layer + prompt & clues) and consumed here as input data: one tagged XML
document per page, no bboxes. A missing or empty file is a refusal.

Decode must NOT lose content: known block tags become blocks, structural
wrappers (opinion, footnotes, ...) are recursed into, and ANY other element
that carries text (author, attorney, ...) is emitted as a block with its
tag preserved — unknown tags are never silently dropped. Mixed-content
text (a wrapper's own leading text, or text trailing a block tag) is
emitted as its own block under the enclosing tag. Detail tags stay
inline: `styled` keeps em/footnotemark as HTML, other inline tags
(citation, a, ...) are unwrapped to their text.

Blocks carry the model's `col`/`x`/`y` attributes when present. The
coordinates are not to scale, but their relative order is a reading-order
clue used by reconstruction.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET

from pipeline.core.config import Dataset
from pipeline.core.markup import gemini_repair

ENGINE = "gemini"

# A parallel-reporter star page (★306): a STRUCTURAL marker gemini emits
# (block-level or inline), not opinion text. Gemini is authoritative for
# it — dots/mistral don't produce it. The marker is PLACED into the
# stream at its position as `★<page>` (2026-07-27: placement by text
# alignment, not just identification): normalization anchors it to the
# preceding token (Token.stars) and keeps it out of the comparison
# keys; the final output re-inserts it where that anchor aligns.
STAR_TAG = "parallelpagenumber"


def _star_marker(pg: str) -> str:
    return f"★{pg}"


# NOTE: `img` is a BLOCK tag even though it nests <p> children — treating
# it as a wrapper would leak in-image text out as ordinary paragraphs.
BLOCK_TAGS = frozenset(
    {
        "pagenumber",
        "parallelpagenumber",
        "p",
        "blockquote",
        "heading",
        "caption",
        "footnote",
        "img",
        "image",
        "table",
    }
)

IMG_TAGS = frozenset({"img", "image"})

# Inline detail tags rendered as presentation HTML; anything else unwraps.
_INLINE_HTML = {
    "em": "em",
    "strong": "strong",
    "u": "u",
    "footnotemark": "sup",
    "sup": "sup",
    "sub": "sub",
}


def load(ds: Dataset, page_id: str) -> str | None:
    """Raw tagged XML, or None if missing (refusal)."""
    f = ds.engine_dir(ENGINE) / f"{page_id}.xml"
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8", errors="replace")


# Markers the API emits instead of XML when generation is blocked.
_REFUSAL_MARKERS = ("RECITATION", "PROHIBITED_CONTENT")


def is_refusal(raw: str | None) -> bool:
    """Missing/empty output, or a non-XML refusal marker payload."""
    if raw is None:
        return True
    stripped = raw.strip()
    if not stripped:
        return True
    return not stripped.startswith("<") and any(
        marker in stripped for marker in _REFUSAL_MARKERS
    )


def _star_page(el: ET.Element) -> str:
    """The page number a star-page element carries (attribute first,
    else the digits of its text)."""
    return el.get("pagenumber") or re.sub(r"\D", "", "".join(el.itertext()))


def _serialize(el: ET.Element, stars: list[str]) -> str:
    """Inline content of an element as sanitized HTML. Inline star-page
    markers are recorded in `stars` AND placed in the stream at their
    position as ★<page>."""
    parts: list[str] = []
    if el.text:
        parts.append(html.escape(el.text, quote=False))
    for child in el:
        if child.tag == STAR_TAG:
            pg = _star_page(child)
            if pg:
                stars.append(pg)
                # padded so the marker is always its own token
                parts.append(f" {_star_marker(pg)} ")
            if child.tail:
                parts.append(html.escape(child.tail, quote=False))
            continue
        mapped = _INLINE_HTML.get(child.tag)
        inner = _serialize(child, stars)
        parts.append(f"<{mapped}>{inner}</{mapped}>" if mapped else inner)
        if child.tail:
            parts.append(html.escape(child.tail, quote=False))
    return "".join(parts)


def _own_text(el: ET.Element) -> str:
    """itertext, with star-page subtrees replaced by their ★<page>
    marker (their tails kept)."""
    parts: list[str] = [el.text or ""]
    for child in el:
        if child.tag == STAR_TAG:
            pg = _star_page(child)
            if pg:
                parts.append(f" {_star_marker(pg)} ")
        else:
            parts.append(_own_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def _has_block_descendant(el: ET.Element) -> bool:
    return any(
        child.tag in BLOCK_TAGS or _has_block_descendant(child) for child in el
    )


def _effective_attrs(el: ET.Element) -> dict:
    """The element's attributes, with col/x/y inherited from the first
    descendant that carries coordinates when the element itself has none
    (e.g. <footnote> holds its coordinates on the nested <p>). Never lose
    a coordinate that exists anywhere inside the block."""
    attrs = dict(el.attrib)
    if "x" in attrs and "y" in attrs:
        return attrs
    for d in el.iter():
        if d is el:
            continue
        if "x" in d.attrib and "y" in d.attrib:
            for key in ("col", "x", "y"):
                if key not in attrs and key in d.attrib:
                    attrs[key] = d.attrib[key]
            break
    return attrs


def _emit(el: ET.Element, out: list[dict]) -> None:
    stars: list[str] = []
    if el.tag == STAR_TAG:
        # a block-level star page: the marker IS the block's content,
        # placed in document order (normalization anchors it and keeps
        # it out of the comparison keys)
        pg = _star_page(el)
        out.append(
            {
                "id": len(out),
                "tag": el.tag,
                "attrs": _effective_attrs(el),
                "text": _star_marker(pg) if pg else "",
                "styled": _star_marker(pg) if pg else "",
                "star_pages": [pg] if pg else [],
            }
        )
        return
    styled = _serialize(el, stars).strip()
    text = " ".join(_own_text(el).split())
    if not text and not stars and el.tag not in IMG_TAGS:
        return
    block = {
        "id": len(out),
        "tag": el.tag,
        "attrs": _effective_attrs(el),
        "text": text,
        "styled": styled,
    }
    if stars:
        block["star_pages"] = stars
    out.append(block)


def _emit_text(fragment: str, tag: str, out: list[dict]) -> None:
    """Mixed-content text — a wrapper's own text (<opinion>PER CURIAM.
    <p>...) or an element tail (<p>...</p>stray text) — emitted as its own
    block under the enclosing tag, so no character of the document is
    lost. Whitespace-only fragments (indentation between tags) are not
    content and are skipped."""
    text = fragment.strip()
    if not text:
        return
    out.append(
        {
            "id": len(out),
            "tag": tag,
            "attrs": {},
            "text": text,
            "styled": html.escape(text, quote=False),
        }
    )


def _walk(el: ET.Element, out: list[dict]) -> None:
    if not len(el):
        # a root with no children still carries its own text
        _emit(el, out)
        return
    _emit_text(el.text or "", el.tag, out)
    for child in el:
        if child.tag in BLOCK_TAGS:
            _emit(child, out)
        elif _has_block_descendant(child):
            _walk(child, out)  # structural wrapper (opinion, footnotes, ...)
        else:
            _emit(child, out)  # content in an unknown tag: never dropped
        _emit_text(child.tail or "", el.tag, out)


def decode(raw: str) -> dict:
    """{"blocks": [{id, tag, attrs, text, styled, star_pages?}],
    "parse_error": ...}. The payload is repaired first (code fences
    stripped, bare & escaped) — both break the parse without touching
    content."""
    try:
        root = ET.fromstring(gemini_repair(raw))
    except ET.ParseError as exc:
        return {"blocks": [], "parse_error": str(exc)}
    blocks: list[dict] = []
    _walk(root, blocks)
    return {"blocks": blocks, "parse_error": None}
