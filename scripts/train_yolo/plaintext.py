"""Temporary: build a plain-text dataset from the bootstrap + base models'
outputs, for fine-tuning a single "all body text" detector.

Everything textual that the bootstrap model tags (paragraphs, headings,
blockquotes, footnotes) is folded into one ``plain_text`` class. Specialized
overlay models (heading, blockquote, ...) carry the semantics separately.

Failure modes addressed here:

  1. The bootstrap model sometimes misses a whole block (usually upper-right).
     The base DocLayout model's ``plain text`` boxes are reliable there, so
     any sufficiently large base box that is mostly uncovered by bootstrap
     content is adopted as a plain_text block. When a base box is mostly
     covered but bootstrap skipped lines inside it (e.g. short Q/A answers),
     the uncovered, ink-bearing strips are adopted individually -- on
     non-caption pages only, since on caption pages the uncovered regions
     are mostly redaction bars and publisher ornaments.
  2. Bootstrap paragraphs often bleed past the top of the footnotes (almost
     always in the right column). The footnote start line (highest bootstrap
     ``footnote`` box top) is computed per page, and any block straddling it
     is split in two. Where possible the split is snapped to the base model's
     more granular ``plain text`` boxes -- the body part ends at the last base
     box above the line and the footnote part starts at the first base box
     below it -- falling back to a plain cut at the line. Both halves are
     kept: footnote text is still plain text.
  3. After merging, contained or duplicated boxes (e.g. a heading inside a
     paragraph, or bootstrap's occasional near-identical double-tags) are
     deduplicated by containment, keeping the larger box.
  4. Bootstrap sometimes misses short headings entirely; the base model's
     ``title`` boxes are adopted the same way as its plain-text boxes (with a
     smaller size floor, since headings are small).
  5. Single-column pages (appendix material etc.) are not our target layout
     and bootstrap's boxes on them are garbage. They are detected by the base
     model emitting multiple near-full-width plain-text blocks, and skipped.
  6. Bootstrap paragraphs sometimes extend one line too far into the next
     paragraph, leaving the two overlapping; the upper box is clipped back to
     the top of the lower one.
  7. Boxes are often a few pixels too tight, cropping bullet glyphs or
     descenders at the edges. When ``EXPAND_BOXES`` is on, each edge is grown
     to cover ink immediately outside it (never intruding on other blocks).

Case-caption pages are included by default (with classes merged, the
bootstrap's caption geometry is usable); pass ``drop_caption_pages=True`` to
train the variant without them.

Labels are returned in the pipeline format keyed for ``train_yolo``, so the
whole thing can be wired up as ``train_yolo(**prepare_plaintext_dataset())``.
"""

import json

import numpy as np
from PIL import Image

from ocr_tools.pagenumber import (
    MAX_HEIGHT_FRAC as PAGENUMBER_MAX_HEIGHT_FRAC,
)
from ocr_tools.pagenumber import (
    MAX_WIDTH_FRAC as PAGENUMBER_MAX_WIDTH_FRAC,
)
from ocr_tools.utils import DATA_DIR, load_data

BOOTSTRAP_NAME = "bootstrap"
BASE_NAME = "base"
CAPTION_MODEL_NAME = "blackletter"
CAPTION_CLASS = "Case Caption"

PLAIN_TEXT_CLASS = "plain_text"
MERGE_CLASSES = ("paragraph", "heading", "blockquote", "footnote")
CONTENT_CLASSES = (*MERGE_CLASSES, "pagenumber")
BASE_PLAIN_CLASS = "plain text"
BASE_TITLE_CLASS = "title"

# Footnote line: ignore footnote tops in the upper page (spurious), and treat
# boxes as crossing the line only when they extend FOOT_TOL past it.
FOOT_LINE_MIN_FRAC = 0.25
FOOT_TOL = 10
FOOT_GAP = 4
# A base box must be mostly inside a straddler to drive the snap.
SNAP_IOA = 0.5
# Orphan adoption: big enough, and mostly uncovered by bootstrap content.
ADOPT_MIN_AREA_FRAC = 0.005
ADOPT_TITLE_MIN_AREA_FRAC = 0.0005
ADOPT_MAX_COVERAGE = 0.25
# Strip adoption: uncovered slices of an otherwise-covered base box must be
# this tall and contain this many ink rows to become blocks. Denser strips
# are redaction bars, not text. Caption pages are excluded entirely: their
# uncovered regions are mostly redaction bars and publisher ornaments.
STRIP_MIN_HEIGHT = 16
STRIP_MIN_INK_ROWS = 8
STRIP_MAX_INK_FRAC = 0.5
# Dedup: drop a box whose area is mostly inside a larger kept box.
DEDUP_IOS = 0.85
MIN_BOX_PX = 8
# Single-column page detection: base plain-text blocks this wide are not
# column blocks; multiple of them means the page isn't our two-column layout.
WIDE_BLOCK_WIDTH_FRAC = 0.55
WIDE_BLOCK_MIN_COUNT = 2
# Vertical overlap clip: the upper box is clipped back to the top of the
# lower one, but only when the overlap is a sliver, not most of the box.
VCLIP_GAP = 4
VCLIP_MAX_FRAC = 0.5
# Ink-snap expansion: grow box edges over ink cut off at the boundary
# (bullets, descenders). Toggle off here if it ever misbehaves.
EXPAND_BOXES = True
EXPAND_INK_THRESHOLD = 160
EXPAND_WINDOW_X = 12
EXPAND_WINDOW_Y = 6
EXPAND_MAX_X = 30
EXPAND_MAX_Y = 12
EXPAND_GAP = 2


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _inter(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)


def _foot_line(boot_dets: list[dict], page_h: int) -> float | None:
    tops = [
        det["box"][1]
        for det in boot_dets
        if det["class"] == "footnote"
        and det["box"][1] > FOOT_LINE_MIN_FRAC * page_h
    ]
    return min(tops) if tops else None


def _oversized_pagenumber(det: dict, page_w: int, page_h: int) -> bool:
    """Bootstrap pagenumber boxes are only trustworthy when small; the giant
    ones (page number + following paragraph swallowed into one box) are a
    known failure mode and must not block orphan adoption."""
    if det["class"] != "pagenumber":
        return False
    x1, y1, x2, y2 = det["box"]
    return (x2 - x1) > PAGENUMBER_MAX_WIDTH_FRAC * page_w or (
        y2 - y1
    ) > PAGENUMBER_MAX_HEIGHT_FRAC * page_h


def _adopt_uncovered_strips(
    det: dict, content: list, ink: np.ndarray
) -> list[dict]:
    """For a base box mostly covered by bootstrap content, adopt the vertical
    strips of it that bootstrap left uncovered -- but only strips that
    actually contain ink (e.g. a skipped Q/A line), never whitespace gaps."""
    x1, y1, x2, y2 = det["box"]
    iy1, iy2 = int(y1), int(y2)
    if iy2 - iy1 <= 0:
        return []
    covered = np.zeros(iy2 - iy1, dtype=bool)
    for ox1, oy1, ox2, oy2 in content:
        xover = min(x2, ox2) - max(x1, ox1)
        if xover < 0.5 * min(x2 - x1, ox2 - ox1):
            continue
        covered[max(int(oy1) - iy1, 0) : max(int(oy2) - iy1, 0)] = True

    strips = []
    start = None
    for i in range(len(covered) + 1):
        open_ = i < len(covered) and not covered[i]
        if open_ and start is None:
            start = i
        elif not open_ and start is not None:
            if i - start >= STRIP_MIN_HEIGHT:
                sy1, sy2 = iy1 + start, iy1 + i
                patch = ink[sy1:sy2, int(x1) : int(x2)]
                rows = np.flatnonzero(patch.any(axis=1))
                if (
                    rows.size >= STRIP_MIN_INK_ROWS
                    and patch.mean() <= STRIP_MAX_INK_FRAC
                ):
                    strips.append(
                        {
                            "class": PLAIN_TEXT_CLASS,
                            "conf": det["conf"],
                            "box": (
                                x1,
                                float(sy1 + rows[0]),
                                x2,
                                float(sy1 + rows[-1] + 1),
                            ),
                        }
                    )
            start = None
    return strips


def _adopt_orphans(
    base_dets: list[dict],
    boot_dets: list[dict],
    page_w: int,
    page_h: int,
    ink: np.ndarray,
    allow_strips: bool = True,
) -> list[dict]:
    """Base-model plain-text boxes covering content the bootstrap missed."""
    content = [
        det["box"]
        for det in boot_dets
        if det["class"] in CONTENT_CLASSES
        and not _oversized_pagenumber(det, page_w, page_h)
    ]
    min_areas = {
        BASE_PLAIN_CLASS: ADOPT_MIN_AREA_FRAC * page_w * page_h,
        BASE_TITLE_CLASS: ADOPT_TITLE_MIN_AREA_FRAC * page_w * page_h,
    }
    adopted = []
    for det in base_dets:
        if det["class"] not in min_areas:
            continue
        box = det["box"]
        area = _area(box)
        if area < min_areas[det["class"]]:
            continue
        coverage = sum(_inter(box, other) for other in content) / area
        if coverage < ADOPT_MAX_COVERAGE:
            adopted.append(
                {
                    "class": PLAIN_TEXT_CLASS,
                    "conf": det["conf"],
                    "box": tuple(box),
                }
            )
        elif allow_strips and det["class"] == BASE_PLAIN_CLASS:
            adopted += _adopt_uncovered_strips(det, content, ink)
    return adopted


def _split_at_foot_line(
    det: dict, foot_line: float, base_plain: list[list]
) -> list[dict]:
    """Split a block straddling the footnote line into body + footnote parts,
    snapping the cut to the base model's granular boxes when possible."""
    x1, y1, x2, y2 = det["box"]
    if y2 <= foot_line + FOOT_TOL or y1 >= foot_line - FOOT_TOL:
        return [det]

    inside = [
        box
        for box in base_plain
        if _area(box) > 0 and _inter(det["box"], box) / _area(box) >= SNAP_IOA
    ]
    above = [box for box in inside if box[3] <= foot_line + FOOT_TOL]
    below = [box for box in inside if box[1] >= foot_line - FOOT_TOL]
    top_end = max((box[3] for box in above), default=foot_line - FOOT_GAP)
    bottom_start = min((box[1] for box in below), default=foot_line + FOOT_GAP)

    parts = []
    if top_end - y1 >= MIN_BOX_PX:
        parts.append({**det, "box": (x1, y1, x2, top_end)})
    if y2 - bottom_start >= MIN_BOX_PX:
        parts.append({**det, "box": (x1, bottom_start, x2, y2)})
    return parts


def _is_single_column(base_dets: list[dict], page_w: int) -> bool:
    """Pages where the base model finds multiple near-full-width text blocks
    aren't our two-column layout."""
    wide = sum(
        1
        for det in base_dets
        if det["class"] == BASE_PLAIN_CLASS
        and det["box"][2] - det["box"][0] > WIDE_BLOCK_WIDTH_FRAC * page_w
    )
    return wide >= WIDE_BLOCK_MIN_COUNT


def _clip_vertical_overlaps(blocks: list[dict]) -> list[dict]:
    """Where a block's bottom dips into the next block down the same column,
    clip the upper block back to the top of the lower one."""
    blocks = sorted(blocks, key=lambda d: d["box"][1])
    clipped = []
    for i, det in enumerate(blocks):
        x1, y1, x2, y2 = det["box"]
        new_y2 = y2
        for other in blocks[i + 1 :]:
            ox1, oy1, ox2, oy2 = other["box"]
            if oy1 - y1 < MIN_BOX_PX or oy1 >= new_y2:
                continue
            xover = min(x2, ox2) - max(x1, ox1)
            if xover < 0.5 * min(x2 - x1, ox2 - ox1):
                continue
            if new_y2 - oy1 > VCLIP_MAX_FRAC * (y2 - y1):
                continue
            new_y2 = oy1 - VCLIP_GAP
        if new_y2 - y1 >= MIN_BOX_PX:
            clipped.append({**det, "box": (x1, y1, x2, new_y2)})
    return clipped


def _expand_blocks(blocks: list[dict], ink: np.ndarray) -> list[dict]:
    """Grow box edges outward over ink sitting just past the boundary (cut
    bullet glyphs, descenders), without intruding on other blocks."""
    if not blocks:
        return blocks
    page_h, page_w = ink.shape
    boxes = [list(det["box"]) for det in blocks]

    def neighbor_limit(idx: int, side: str) -> float:
        x1, y1, x2, y2 = boxes[idx]
        lim = {"left": 0, "top": 0, "right": page_w, "bottom": page_h}[side]
        for j, (ox1, oy1, ox2, oy2) in enumerate(boxes):
            if j == idx:
                continue
            if side in ("left", "right"):
                if min(y2, oy2) - max(y1, oy1) <= 0:
                    continue
                if side == "left" and ox2 <= x1:
                    lim = max(lim, ox2 + EXPAND_GAP)
                elif side == "right" and ox1 >= x2:
                    lim = min(lim, ox1 - EXPAND_GAP)
            else:
                if min(x2, ox2) - max(x1, ox1) <= 0:
                    continue
                if side == "top" and oy2 <= y1:
                    lim = max(lim, oy2 + EXPAND_GAP)
                elif side == "bottom" and oy1 >= y2:
                    lim = min(lim, oy1 - EXPAND_GAP)
        return lim

    def grow(idx: int, side: str, window: int, max_grow: int) -> None:
        x1, y1, x2, y2 = (int(round(c)) for c in boxes[idx])
        outward = side in ("left", "top")
        cur = {"left": x1, "right": x2, "top": y1, "bottom": y2}[side]
        if outward:
            lim = int(max(neighbor_limit(idx, side), cur - max_grow))
        else:
            lim = int(min(neighbor_limit(idx, side), cur + max_grow))
        while (cur > lim) if outward else (cur < lim):
            if outward:
                lo, hi = max(lim, cur - window), cur
            else:
                lo, hi = cur, min(lim, cur + window)
            if side in ("left", "right"):
                strip = ink[y1:y2, lo:hi]
                hits = np.flatnonzero(strip.any(axis=0))
            else:
                strip = ink[lo:hi, x1:x2]
                hits = np.flatnonzero(strip.any(axis=1))
            if hits.size == 0:
                break
            cur = lo + int(hits[0] if outward else hits[-1] + 1)
        edge = {"left": 0, "top": 1, "right": 2, "bottom": 3}[side]
        boxes[idx][edge] = cur

    for idx in range(len(boxes)):
        grow(idx, "left", EXPAND_WINDOW_X, EXPAND_MAX_X)
        grow(idx, "right", EXPAND_WINDOW_X, EXPAND_MAX_X)
        grow(idx, "top", EXPAND_WINDOW_Y, EXPAND_MAX_Y)
        grow(idx, "bottom", EXPAND_WINDOW_Y, EXPAND_MAX_Y)

    return [{**det, "box": tuple(box)} for det, box in zip(blocks, boxes)]


def _dedup(blocks: list[dict]) -> list[dict]:
    kept = []
    for det in sorted(blocks, key=lambda d: _area(d["box"]), reverse=True):
        area = _area(det["box"])
        if area <= 0:
            continue
        if any(
            _inter(det["box"], other["box"]) / area >= DEDUP_IOS
            for other in kept
        ):
            continue
        kept.append(det)
    return kept


def page_blocks(
    boot_dets: list[dict],
    base_dets: list[dict],
    image: Image.Image,
    is_caption_page: bool = False,
) -> list[dict]:
    """Merged, gap-filled, footnote-split, deduplicated, overlap-clipped
    (and optionally ink-expanded) plain_text blocks."""
    page_w, page_h = image.size
    ink = np.asarray(image.convert("L")) < EXPAND_INK_THRESHOLD
    blocks = [
        {
            "class": PLAIN_TEXT_CLASS,
            "conf": det["conf"],
            "box": tuple(det["box"]),
        }
        for det in boot_dets
        if det["class"] in MERGE_CLASSES
    ]
    blocks += _adopt_orphans(
        base_dets,
        boot_dets,
        page_w,
        page_h,
        ink,
        allow_strips=not is_caption_page,
    )

    foot_line = _foot_line(boot_dets, page_h)
    if foot_line is not None:
        base_plain = [
            det["box"] for det in base_dets if det["class"] == BASE_PLAIN_CLASS
        ]
        blocks = [
            part
            for det in blocks
            for part in _split_at_foot_line(det, foot_line, base_plain)
        ]
    blocks = _clip_vertical_overlaps(_dedup(blocks))
    if EXPAND_BOXES:
        blocks = _expand_blocks(blocks, ink)
    return blocks


def prepare_plaintext_dataset(
    train_split: str = "train",
    eval_split: str = "val",
    drop_caption_pages: bool = False,
) -> dict:
    """Build the plain-text dataset.

    Returns a dict with ``train_images``/``eval_images`` (image paths) and
    ``train_labels``/``eval_labels`` (pipeline-format labels), ready to splat
    into ``train_yolo``.
    """
    out = {}
    for role, split in [("train", train_split), ("eval", eval_split)]:
        images, labels = [], []
        for pdf_path in load_data(split)["path"].tolist():
            stem = pdf_path.stem
            image_path = pdf_path.parent / f"{stem}.jpg"
            boot_path = pdf_path.parent / f"{stem}.yolo.{BOOTSTRAP_NAME}.json"
            base_path = pdf_path.parent / f"{stem}.yolo.{BASE_NAME}.json"
            caption_path = (
                pdf_path.parent / f"{stem}.yolo.{CAPTION_MODEL_NAME}.json"
            )
            needed = [image_path, boot_path, base_path, caption_path]
            if not all(path.exists() for path in needed):
                continue
            captions = json.loads(caption_path.read_text())
            is_caption_page = any(
                det["class"] == CAPTION_CLASS for det in captions
            )
            if drop_caption_pages and is_caption_page:
                continue
            image = Image.open(image_path)
            base_dets = json.loads(base_path.read_text())
            if _is_single_column(base_dets, image.width):
                continue
            blocks = page_blocks(
                json.loads(boot_path.read_text()),
                base_dets,
                image,
                is_caption_page=is_caption_page,
            )
            images.append(image_path)
            labels.append(blocks)
        out[f"{role}_images"] = images
        out[f"{role}_labels"] = labels
    return out


PREVIEW_NAME = "plaintext_train"


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preview",
        action="store_true",
        help="instead of training, write the prepared labels next to the "
        f"images as {{stem}}.yolo.{PREVIEW_NAME}.json so the viewer "
        "picks them up",
    )
    parser.add_argument("--drop-caption-pages", action="store_true")
    args = parser.parse_args()

    dataset = prepare_plaintext_dataset(
        drop_caption_pages=args.drop_caption_pages
    )
    print(
        f"train: {len(dataset['train_images'])} examples, "
        f"eval: {len(dataset['eval_images'])} examples"
    )

    if args.preview:
        # Clear stale preview files so re-scoped runs don't leave
        # out-of-scope examples behind in the viewer.
        old_paths = sorted(
            (DATA_DIR / "pages").glob(f"*.yolo.{PREVIEW_NAME}.json")
        )
        for old_path in old_paths:
            old_path.unlink()
        print(f"deleted {len(old_paths)} old preview files")
        written = 0
        for role in ["train", "eval"]:
            for image_path, label in zip(
                dataset[f"{role}_images"], dataset[f"{role}_labels"]
            ):
                out_path = (
                    image_path.parent
                    / f"{image_path.stem}.yolo.{PREVIEW_NAME}.json"
                )
                out_path.write_text(json.dumps(label))
                written += 1
        print(f"wrote {written} *.yolo.{PREVIEW_NAME}.json files")
    else:
        from ocr_tools.yolo import download_base_model, train_yolo

        download_base_model()
        train_yolo(**dataset, name="plaintext", epochs=10)
