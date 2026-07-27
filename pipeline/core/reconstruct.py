"""Stage 5 — reconstruction: place each engine's blocks/lines into reading
order using the container layout. Text is used RAW here — normalization
(stage 6) applies afterwards and never changes ordering.

- Block/region engines (dots, mistral, surya block mode — main OR
  supplemental; every block engine is treated identically): each bbox is
  assigned a container (role) and column via layout.assign, then ordered
  page_number -> body (column, y, x) -> footnotes via
  layout.reading_order. An image block (`Picture`/`Figure`/`image`) keeps
  its reading position but contributes NO text; a near-black image block
  is a redaction placeholder and is excluded. A supplemental text block
  mostly inside a MAIN-engine image block is in-image text (captions on
  figures) and is omitted. surya_line as MAIN reconstructs the same way
  (each line placed as its own item).
- Gemini (no bboxes): blocks order by their col/x/y attributes when
  present — the coordinates are not to scale, but their relative order is
  a reliable reading-order clue; blocks without coordinates keep their
  document position. Text inside an <image> tag is omitted (consistent
  with the other engines); star pages are reported, never content.
- Surya lines as SUPPLEMENTAL: each line is matched to a main-engine
  block — kept when a block covers >= COVER_T of the line's area, or >=
  CENTER_COVER_T with the line's center inside the block (surya draws
  looser boxes; the center test rescues true content at the block edge
  without admitting bleed-through, which fails both). Lines matched to a
  main image block are in-image text and are omitted; unmatched lines are
  bleed-through hallucinations and are dropped. Kept lines are grouped by
  their main block and joined back into paragraphs, so the line stream
  reads at the same block granularity as the main engine.
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

# An image block whose crop is at least this fraction near-black is a
# REDACTION placeholder, not a real figure -> excluded from the
# reconstruction and reported (real figures observed <=0.744,
# redactions >=0.818). run.py stamps `black_frac` on image blocks.
REDACTION_BLACK_T = 0.80

# Surya line-keeping thresholds (see module docstring).
COVER_T = 0.5
CENTER_COVER_T = 0.25


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


def _is_image(b: dict) -> bool:
    return (b.get("label") or b.get("type")) in IMAGE_LABELS


def reconstruct_blocks(
    containers: dict,
    blocks: list[dict],
    main_blocks: list[dict] | None = None,
) -> dict:
    """Reading-order reconstruction for ANY block/region engine (dots,
    mistral, surya block mode — as main or supplemental; every block
    engine is treated identically, 2026-07-27). Image blocks whose crop
    is near-black are redaction placeholders, not figures — excluded
    and reported. With `main_blocks` (supplemental use): a text block
    mostly inside one of the MAIN engine's image blocks is in-image
    text (captions on figures) — omitted and reported; the image
    renders as-is in the final."""
    redactions = [
        b["id"]
        for b in blocks
        if _is_image(b) and (b.get("black_frac") or 0.0) >= REDACTION_BLACK_T
    ]
    pics = [
        b["bbox"] for b in main_blocks or [] if _is_image(b) and b.get("bbox")
    ]
    in_image = [
        b["id"]
        for b in blocks
        if not _is_image(b)
        and b.get("bbox")
        and any(geometry.cover_frac(b["bbox"], p) >= COVER_T for p in pics)
    ]
    skip = set(redactions) | set(in_image)
    items = [
        {"id": b["id"], "bbox": b.get("bbox"), "image": _is_image(b)}
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


def classify_surya_lines(
    lines: list[dict], dots_blocks: list[dict]
) -> tuple[dict[int, int], list[int], list[int]]:
    """Match each line to a dots block. Returns (assigned line_id ->
    dots_block_id, in_image line ids, dropped line ids)."""
    blocks = [
        (b["id"], b.get("label", ""), b["bbox"])
        for b in dots_blocks
        if b.get("bbox")
    ]
    assigned: dict[int, int] = {}
    in_image: list[int] = []
    dropped: list[int] = []
    for line in lines:
        bb = line.get("bbox")
        best: tuple[float, int, str] | None = None
        if bb:
            for bid, label, obb in blocks:
                cov = geometry.cover_frac(bb, obb)
                ok = cov >= COVER_T or (
                    cov >= CENTER_COVER_T and geometry.center_in(bb, obb)
                )
                if ok and (best is None or cov > best[0]):
                    best = (cov, bid, label)
        if best is None:
            dropped.append(line["id"])
        elif best[2] in IMAGE_LABELS:
            in_image.append(line["id"])
        else:
            assigned[line["id"]] = best[1]
    return assigned, in_image, dropped


def reconstruct_surya(
    containers: dict, lines: list[dict], dots_blocks: list[dict]
) -> dict:
    """Filter lines against dots blocks, group survivors into paragraphs
    (one per dots block), then order the paragraphs like blocks."""
    assigned, in_image, dropped = classify_surya_lines(lines, dots_blocks)
    by_block: dict[int, list[dict]] = {}
    for line in lines:
        bid = assigned.get(line["id"])
        if bid is not None:
            by_block.setdefault(bid, []).append(line)
    bbox_of = {b["id"]: b.get("bbox") for b in dots_blocks}
    paragraphs = []
    text_by_id: dict[int, str] = {}
    html_by_id: dict[int, str] = {}
    lines_of: dict[int, list[int]] = {}
    for bid, members in by_block.items():
        members.sort(key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
        paragraphs.append({"id": bid, "bbox": bbox_of[bid], "image": False})
        lines_of[bid] = [ln["id"] for ln in members]
        # each member IS a physical line — the line breaks are preserved
        # (newline-joined, not flattened; 2026-07-27)
        text_by_id[bid] = "\n".join(
            ln.get("text", "").strip() for ln in members
        ).strip()
        html_by_id[bid] = "\n".join(
            ln.get("styled", ln.get("text", "")).strip() for ln in members
        ).strip()
    ordered, no_bbox = _place(containers, paragraphs)
    result = _finish(ordered, text_by_id, html_by_id)
    # Line->block provenance: each paragraph records its member surya
    # lines in join order, so a char span in the paragraph text can be
    # traced back to the line that produced it.
    for it in result["items"]:
        it["lines"] = lines_of.get(it["id"], [])
    result["no_bbox"] = no_bbox
    result["dropped"] = dropped
    result["in_image"] = in_image
    return result
