"""Page rendering: the black-redacted 1700x2200 PNG every engine saw.
Rendered once and cached under the dataset's page_png/ (the bundled
datasets ship with the cache pre-populated, so the cached engine outputs
and the render are guaranteed to match)."""

from __future__ import annotations

from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from pipeline.core import readers
from pipeline.core.config import RENDER_H, RENDER_W, Dataset
from pipeline.core.pages import Page


def page_png(ds: Dataset, page: Page) -> Path:
    out = ds.page_png / f"{page.page_id}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(page.src_pdf)
    try:
        pg = doc[page.page_index]
        zoom = RENDER_W / pg.rect.width
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        doc.close()
    if img.size != (RENDER_W, RENDER_H):
        img = img.resize((RENDER_W, RENDER_H))
    draw = ImageDraw.Draw(img)
    for r in readers.redaction_rects(ds, page):
        x0, y0, x1, y1 = r["bbox"]
        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
    img.save(out)
    return out


def black_fraction(ds: Dataset, page: Page, bbox: list[float]) -> float:
    """Fraction of near-black pixels in a bbox crop — distinguishes a
    redaction placeholder (~0.82-0.97 observed) from a real scanned figure
    (<=0.74 observed)."""
    img = Image.open(page_png(ds, page)).convert("L")
    x0, y0, x1, y1 = (int(v) for v in bbox)
    left = max(0, min(x0, img.width - 1))
    top = max(0, min(y0, img.height - 1))
    crop = img.crop(
        (
            left,
            top,
            min(img.width, max(x1, left + 1)),
            min(img.height, max(y1, top + 1)),
        )
    )
    hist = crop.histogram()  # 256 grayscale bins
    total = crop.width * crop.height
    return sum(hist[:40]) / total if total else 0.0
