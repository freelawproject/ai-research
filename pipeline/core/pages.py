"""Page discovery from a dataset's sources. Three layouts:

- volume tree — redacted/<reporter>/<volume>/<first_page>/ holding the
  volume PDF; page_id = "{reporter}.{volume}.{first_page}__p{index}".
- flat — redacted/ holding one single-page redacted PDF per page
  (sampled sets); page_id = the PDF's stem.
- prerendered — no redacted/ at all: the set ships its page renders
  under page_png/ and the whole-volume PDFs under source/. page_id is
  the PNG's stem, "{volume_stem}__page_{n}" with n 1-based. UNREDACTED
  sets arrive this way — nothing was blacked out, so there is no
  per-page redacted PDF and no rects to burn in.

Either way, the page_id is the key every engine output and container
prediction uses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import fitz

from pipeline.core.config import Dataset

# "<volume stem>__page_<1-based number>" — what the image renderer
# names a whole volume's pages.
PRERENDERED_RE = re.compile(r"^(?P<volume>.+)__page_(?P<number>\d+)$")


@dataclass(frozen=True)
class Page:
    page_id: str
    reporter: str
    volume: str
    first_page: str
    page_index: int  # 0-based within the volume's source PDF
    vol_dir: Path  # <redacted>/<reporter>/<volume>/<first_page>/
    src_pdf: Path  # the un-redacted source PDF


def _src_pdf(vol_dir: Path) -> Path | None:
    cands = [
        p
        for p in sorted(vol_dir.glob("*.pdf"))
        if not p.name.endswith(".redacted.pdf")
    ]
    return cands[0] if cands else None


def _page_count(pdf: Path) -> int:
    doc = fitz.open(pdf)
    try:
        return doc.page_count
    finally:
        doc.close()


def _name_parts(stem: str) -> tuple[str, str, str]:
    """(reporter, volume, first_page) read off a dotted stem. Labels
    only — nothing downstream keys on them."""
    rep, _, rest = stem.partition(".")
    vol, _, fp = rest.partition(".")
    return rep, vol, fp


def _prerendered(ds: Dataset) -> list[Page]:
    """Pages of a set that ships its renders. The render is the source
    of truth here, so src_pdf is only a provenance pointer: page_png/
    is complete by construction (discovery reads it), which is why
    render.page_png never has to reopen the volume."""
    pages: list[Page] = []
    for png in sorted(ds.page_png.glob("*.png")):
        m = PRERENDERED_RE.match(png.stem)
        if m is None:
            continue
        volume_stem = m.group("volume")
        rep, vol, fp = _name_parts(volume_stem)
        pages.append(
            Page(
                page_id=png.stem,
                reporter=rep,
                volume=vol,
                first_page=fp,
                page_index=int(m.group("number")) - 1,
                vol_dir=ds.source,
                src_pdf=ds.source / f"{volume_stem}.pdf",
            )
        )
    return pages


@lru_cache(maxsize=8)
def _discover(ds: Dataset) -> tuple[Page, ...]:
    redacted = ds.redacted
    pages: list[Page] = []
    for det in sorted(redacted.glob("*/*/*/detections.json")):
        vol_dir = det.parent
        src = _src_pdf(vol_dir)
        if src is None:
            continue
        rep = vol_dir.parent.parent.name
        vol = vol_dir.parent.name
        fp = vol_dir.name
        for i in range(_page_count(src)):
            pages.append(
                Page(
                    page_id=f"{rep}.{vol}.{fp}__p{i}",
                    reporter=rep,
                    volume=vol,
                    first_page=fp,
                    page_index=i,
                    vol_dir=vol_dir,
                    src_pdf=src,
                )
            )
    if pages:
        return tuple(pages)
    # flat layout: one single-page redacted PDF per page, the stem is
    # the page id (redaction is baked into the PDF, so there are no
    # per-volume rects to consult)
    for pdf in sorted(redacted.glob("*.pdf")):
        rep, vol, fp = _name_parts(pdf.stem)
        pages.append(
            Page(
                page_id=pdf.stem,
                reporter=rep,
                volume=vol,
                first_page=fp,
                page_index=0,
                vol_dir=redacted,
                src_pdf=pdf,
            )
        )
    if pages:
        return tuple(pages)
    return tuple(_prerendered(ds))


def discover_pages(ds: Dataset) -> list[Page]:
    return list(_discover(ds))


def get_page(ds: Dataset, page_id: str) -> Page | None:
    return next((p for p in _discover(ds) if p.page_id == page_id), None)
