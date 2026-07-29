"""Page discovery over the two dataset layouts: volume trees and flat
per-page redacted PDFs."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

import fitz

from pipeline.core.config import Dataset
from pipeline.core.pages import discover_pages


def _one_page_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


class PageDiscoveryTest(TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="pages-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "redacted").mkdir(parents=True)

    def _ds(self) -> Dataset:
        return Dataset(name=self.tmp.name, root=self.tmp)

    def test_flat_layout_uses_the_pdf_stem(self) -> None:
        for stem in (
            "a3d.214.0259-0274__page_008",
            "sw3d.1.2__page_001",
        ):
            _one_page_pdf(self.tmp / "redacted" / f"{stem}.pdf")
        pages = discover_pages(self._ds())
        self.assertEqual(
            [p.page_id for p in pages],
            ["a3d.214.0259-0274__page_008", "sw3d.1.2__page_001"],
        )
        p = pages[0]
        self.assertEqual(p.page_index, 0)
        self.assertEqual(p.reporter, "a3d")
        self.assertEqual(p.volume, "214")
        self.assertTrue(p.src_pdf.name.endswith("page_008.pdf"))

    def test_volume_tree_wins_over_stray_flat_pdfs(self) -> None:
        vol = self.tmp / "redacted" / "rep" / "9" / "9"
        vol.mkdir(parents=True)
        (vol / "detections.json").write_text("[]")
        _one_page_pdf(vol / "vol.pdf")
        _one_page_pdf(self.tmp / "redacted" / "stray.pdf")
        pages = discover_pages(self._ds())
        self.assertEqual([p.page_id for p in pages], ["rep.9.9__p0"])
