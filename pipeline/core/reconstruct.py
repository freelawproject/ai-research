"""Reconstruction: place each engine's blocks/lines into reading
order using the container layout. Text is used RAW here — normalization
applies afterwards and never changes ordering.

- Block/region engines (dots, mistral, surya block mode — main OR
  supplemental; every block engine is treated identically): each bbox is
  assigned a container (role) and column via layout.assign, then ordered
  page_number -> body (column, y, x) -> footnotes via
  layout.reading_order. An image block (`Picture`/`Figure`/`image`) keeps
  its reading position but contributes NO text; a near-black image block
  is a redaction placeholder and is excluded. A supplemental text block
  mostly inside a MAIN-engine image block is in-image text (captions on
  figures) and is omitted.
- Gemini (no bboxes): blocks order by their col/x/y attributes when
  present — the coordinates are not to scale, but their relative order is
  a reliable reading-order clue; blocks without coordinates keep their
  document position. Text inside an <image> tag is omitted (consistent
  with the other engines); star pages are reported, never content.

"""

from __future__ import annotations

from pipeline.core import geometry, layout

# Container label -> the role a block inside it carries.
ROLE_OF_CONTAINER = {
    "page_number": "page_number",
    "footnote_block": "footnote",
    "blockquote": "blockquote",
    "heading": "heading",
    "caption": "content",
    "column": "content",
    None: "content",
}

# Gemini block tag -> role.
ROLE_OF_TAG = {
    "pagenumber": "page_number",
    "footnote": "footnote",
    "blockquote": "blockquote",
    "heading": "heading",
    "caption": "content",
    "p": "content",
    "table": "content",
}

# Engine labels that mark an image block (no text contribution).
IMAGE_LABELS = frozenset({"Picture", "Figure", "image"})

# A block whose crop is at least this fraction near-black is REDACTION
# territory, not content: a redaction-placeholder image, or text an
# engine hallucinated over a redacted region (mistral invents whole
# passages there) -> excluded from the reconstruction and reported
# (real figures observed <=0.744, real text far lower; redactions
# >=0.812). run.py stamps `black_frac` on every bbox-carrying block.
REDACTION_BLACK_T = 0.80

# A supplemental text block is in-image when a main image block covers
# at least this fraction of its area.
COVER_T = 0.5


def _place(
    containers: dict, items: list[dict]
) -> tuple[list[dict], list[int]]:
    """Assign role/band/column to bbox-carrying items and order them.
    Returns (ordered items, ids of items that had no bbox and could not
    be placed — reported, never silently lost)."""
    placed = []
    no_bbox: list[int] = []
    for it in items:
        if not it.get("bbox"):
            no_bbox.append(it["id"])
            continue
        hit, side = layout.assign(containers, it["bbox"])
        role = (
            "image"
            if it.get("image")
            else ROLE_OF_CONTAINER.get(hit, "content")
        )
        placed.append(
            {
                "id": it["id"],
                "bbox": it["bbox"],
                "role": role,
                "band": layout.band_of(role),
                "column": side,
            }
        )
    return layout.reading_order(placed), no_bbox


def _finish(
    ordered: list[dict],
    text_by_id: dict[int, str],
    html_by_id: dict[int, str],
) -> dict:
    items = [
        {
            "id": p["id"],
            "role": p["role"],
            "band": p["band"],
            "column": p["column"],
            "text": ""
            if p["role"] == "image"
            else text_by_id.get(p["id"], ""),
            "html": ""
            if p["role"] == "image"
            else html_by_id.get(p["id"], ""),
        }
        for p in ordered
    ]
    text = "\n".join(
        text_by_id.get(p["id"], "") for p in ordered if p["role"] != "image"
    ).strip()
    return {"order": [p["id"] for p in ordered], "items": items, "text": text}


def is_image(b: dict) -> bool:
    return (b.get("label") or b.get("type")) in IMAGE_LABELS


def reconstruct_blocks(
    containers: dict,
    blocks: list[dict],
    main_blocks: list[dict] | None = None,
) -> dict:
    """Reading-order reconstruction for ANY block/region engine (dots,
    mistral, surya block mode — as main or supplemental; every block
    engine is treated identically). A near-black block —
    image OR text — is redaction territory (placeholder image, or text
    hallucinated over a redacted region): excluded and reported. With
    `main_blocks` (supplemental use): a text block mostly inside one
    of the MAIN engine's image blocks is in-image text (captions on
    figures) — omitted and reported; the image renders as-is in the
    final."""
    redactions = [
        b["id"]
        for b in blocks
        if (b.get("black_frac") or 0.0) >= REDACTION_BLACK_T
    ]
    pics = [
        b["bbox"] for b in main_blocks or [] if is_image(b) and b.get("bbox")
    ]
    in_image = [
        b["id"]
        for b in blocks
        if not is_image(b)
        and b.get("bbox")
        and any(geometry.cover_frac(b["bbox"], p) >= COVER_T for p in pics)
    ]
    skip = set(redactions) | set(in_image)
    items = [
        {"id": b["id"], "bbox": b.get("bbox"), "image": is_image(b)}
        for b in blocks
        if b["id"] not in skip
    ]
    ordered, no_bbox = _place(containers, items)
    result = _finish(
        ordered,
        {b["id"]: b.get("text", "") for b in blocks},
        {b["id"]: b.get("styled", b.get("text", "")) for b in blocks},
    )
    result["no_bbox"] = no_bbox
    result["redactions"] = redactions
    if main_blocks is not None:
        result["in_image"] = in_image
    return result


def _coord(attrs: dict, key: str) -> float | None:
    try:
        return float(attrs[key])
    except (KeyError, TypeError, ValueError):
        return None


def _sort_by_coords(band_items: list[dict]) -> list[dict]:
    """Order a band by (column, y, x) from the model's own coordinates.
    Not-to-scale is fine — only relative order matters. An item without
    coordinates inherits its predecessor's position (keeps document
    order locally)."""
    keyed = []
    last = (0, -1.0, 0.0)
    for it in band_items:  # document order
        colrank = {"L": 0, "R": 1}.get(it["column"], last[0])
        y = it["_y"] if it["_y"] is not None else last[1] + 0.001
        x = it["_x"] if it["_x"] is not None else last[2]
        key = (colrank, y, x)
        last = key
        keyed.append((key, it))
    keyed.sort(key=lambda t: t[0])
    return [it for _, it in keyed]


GEMINI_IMG_TAGS = frozenset({"img", "image"})


def reconstruct_gemini(blocks: list[dict]) -> dict:
    """Gemini emits no bboxes; order by its col/x/y attributes per band,
    document order where coordinates are absent. Text inside <img> tags
    is in-image text (captions on figures) and is omitted. Star pages
    (parallelpagenumber) are PLACED at their position as ★<page>
    markers (block-level markers keep their document position; inline
    ones sit inside their block's text) and reported; normalization
    anchors them to the preceding token and keeps them out of the
    comparison keys."""
    omitted = [b["id"] for b in blocks if b.get("tag") in GEMINI_IMG_TAGS]
    star_pages = [pg for b in blocks for pg in b.get("star_pages", []) if pg]
    usable = [b for b in blocks if b.get("tag") not in GEMINI_IMG_TAGS]
    staged: dict[str, list[dict]] = {
        "page_number": [],
        "body": [],
        "footnote": [],
    }
    for b in usable:
        role = ROLE_OF_TAG.get(b.get("tag", ""), "content")
        band = layout.band_of(role)
        attrs = b.get("attrs", {})
        staged[band].append(
            {
                "id": b["id"],
                "role": role,
                "band": band,
                "column": attrs.get("col"),
                "_x": _coord(attrs, "x"),
                "_y": _coord(attrs, "y"),
            }
        )
    ordered = (
        staged["page_number"]
        + _sort_by_coords(staged["body"])
        + _sort_by_coords(staged["footnote"])
    )
    for it in ordered:
        it.pop("_x", None)
        it.pop("_y", None)
    result = _finish(
        ordered,
        {b["id"]: b.get("text", "") for b in usable},
        {b["id"]: b.get("styled", b.get("text", "")) for b in usable},
    )
    result["omitted"] = omitted
    result["star_pages"] = star_pages
    return result
