"""Stage 5 — reconstruction: place each engine's blocks/lines into reading
order using the container layout. Text is used RAW here — normalization
(stage 6) applies afterwards and never changes ordering.

- Block engines (dots, mistral): each bbox is assigned a container (role)
  and column via layout.assign, then ordered page_number -> body (column,
  y, x) -> footnotes via layout.reading_order. An image block (dots
  `Picture`, mistral `image`) keeps its reading position but contributes NO
  text — the image itself is rendered in the final product.
- Gemini (no bboxes): blocks order by their col/x/y attributes when
  present — the coordinates are not to scale, but their relative order is
  a reliable reading-order clue; blocks without coordinates keep their
  document position. Text inside an <image> tag is omitted (consistent
  with the other engines).
- Surya (lines): each line is matched to a dots block — kept when a block
  covers >= COVER_T of the line's area, or >= CENTER_COVER_T with the
  line's center inside the block (surya draws looser boxes than dots; the
  center test rescues true content at the block edge without admitting
  bleed-through, which fails both). Lines matched to a dots Picture block
  are in-image text (captions on figures) and are omitted; unmatched lines
  are bleed-through hallucinations and are dropped. Kept lines are grouped
  by their dots block and joined back into paragraphs, so the surya route
  reads at the same block granularity as dots.
"""

from __future__ import annotations

from pipeline.core import layout

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
    "parallelpagenumber": "page_number",
    "footnote": "footnote",
    "blockquote": "blockquote",
    "heading": "heading",
    "caption": "content",
    "p": "content",
    "table": "content",
}

# Engine labels that mark an image block (no text contribution).
IMAGE_LABELS = frozenset({"Picture", "Figure", "image"})

# Surya line-keeping thresholds (see module docstring).
COVER_T = 0.5
CENTER_COVER_T = 0.25


def _area(bb: list[float]) -> float:
    return max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])


def _cover_frac(inner: list[float], outer: list[float]) -> float:
    """Fraction of inner's area that lies inside outer (0..1)."""
    ix0, iy0 = max(inner[0], outer[0]), max(inner[1], outer[1])
    ix1, iy1 = min(inner[2], outer[2]), min(inner[3], outer[3])
    overlap = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    return overlap / max(1.0, _area(inner))


def _center_in(inner: list[float], outer: list[float]) -> bool:
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _place(containers: dict, items: list[dict]) -> list[dict]:
    """Assign role/band/column to bbox-carrying items and order them."""
    placed = []
    for it in items:
        if not it.get("bbox"):
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
    return layout.reading_order(placed)


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
            "html": "" if p["role"] == "image" else html_by_id.get(
                p["id"], ""
            ),
        }
        for p in ordered
    ]
    text = "\n".join(
        text_by_id.get(p["id"], "")
        for p in ordered
        if p["role"] != "image"
    ).strip()
    return {"order": [p["id"] for p in ordered], "items": items, "text": text}


def reconstruct_blocks(containers: dict, blocks: list[dict]) -> dict:
    """Reading-order reconstruction for a block engine (dots, mistral)."""
    items = [
        {
            "id": b["id"],
            "bbox": b.get("bbox"),
            "image": (b.get("label") or b.get("type")) in IMAGE_LABELS,
        }
        for b in blocks
    ]
    ordered = _place(containers, items)
    return _finish(
        ordered,
        {b["id"]: b.get("text", "") for b in blocks},
        {b["id"]: b.get("styled", b.get("text", "")) for b in blocks},
    )


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
    document order where coordinates are absent. Text inside <img> tags is
    in-image text (captions on figures) and is omitted."""
    omitted = [b["id"] for b in blocks if b.get("tag") in GEMINI_IMG_TAGS]
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
                cov = _cover_frac(bb, obb)
                ok = cov >= COVER_T or (
                    cov >= CENTER_COVER_T and _center_in(bb, obb)
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


def reconstruct_surya_blocks(
    containers: dict, blocks: list[dict], dots_blocks: list[dict]
) -> dict:
    """Surya BLOCK variant: reconstruct like a block engine. A surya block
    mostly inside a dots Picture block is in-image text and is omitted; no
    bleed-through filter (whole-page block mode has context and does not
    ghost like line mode). Surya's own Picture/Figure blocks become image
    items via IMAGE_LABELS."""
    pics = [
        b["bbox"]
        for b in dots_blocks
        if b.get("label") in IMAGE_LABELS and b.get("bbox")
    ]
    in_image = [
        b["id"]
        for b in blocks
        if b.get("bbox")
        and b.get("label") not in IMAGE_LABELS
        and any(_cover_frac(b["bbox"], p) >= COVER_T for p in pics)
    ]
    skip = set(in_image)
    result = reconstruct_blocks(
        containers, [b for b in blocks if b["id"] not in skip]
    )
    result["in_image"] = in_image
    return result


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
    for bid, members in by_block.items():
        members.sort(key=lambda ln: (ln["bbox"][1], ln["bbox"][0]))
        paragraphs.append({"id": bid, "bbox": bbox_of[bid], "image": False})
        text_by_id[bid] = " ".join(
            ln.get("text", "").strip() for ln in members
        ).strip()
        html_by_id[bid] = " ".join(
            ln.get("styled", ln.get("text", "")).strip() for ln in members
        ).strip()
    ordered = _place(containers, paragraphs)
    result = _finish(ordered, text_by_id, html_by_id)
    result["dropped"] = dropped
    result["in_image"] = in_image
    return result
