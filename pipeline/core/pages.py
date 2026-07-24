"""Page discovery from a dataset's redacted volume tree.

page_id = "{reporter}.{volume}.{first_page}__p{index}" — the id every engine
output and container prediction is keyed by.
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
    return tuple(pages)


def discover_pages(ds: Dataset) -> list[Page]:
    return list(_discover(ds.redacted))


def get_page(ds: Dataset, page_id: str) -> Page | None:
    return next(
        (p for p in _discover(ds.redacted) if p.page_id == page_id), None
    )
