"""Page discovery from a dataset's redacted sources. Two layouts:

- volume tree — redacted/<reporter>/<volume>/<first_page>/ holding the
  volume PDF; page_id = "{reporter}.{volume}.{first_page}__p{index}".
- flat — redacted/ holding one single-page redacted PDF per page
  (sampled sets); page_id = the PDF's stem.

Either way, the page_id is the key every engine output and container
prediction uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import fitz

from pipeline.core.config import Dataset


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


@lru_cache(maxsize=8)
def _discover(redacted: Path) -> tuple[Page, ...]:
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
        rep, _, rest = pdf.stem.partition(".")
        vol, _, fp = rest.partition(".")
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
    return tuple(pages)


def discover_pages(ds: Dataset) -> list[Page]:
    return list(_discover(ds.redacted))


def get_page(ds: Dataset, page_id: str) -> Page | None:
    return next(
        (p for p in _discover(ds.redacted) if p.page_id == page_id), None
    )
