import unittest
from collections import Counter

from pipeline.core.layout import _columns, reading_order
from pipeline.core.postprocess import postprocess_dets


class PostprocessTest(unittest.TestCase):
    def test_same_class_duplicates_deduped(self) -> None:
        dets = [
            {"label": "column", "bbox": [0, 0, 100, 500], "confidence": 0.9},
            {"label": "column", "bbox": [2, 2, 98, 495], "confidence": 0.4},
        ]
        stats: Counter = Counter()
        out = postprocess_dets(dets, stats)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["confidence"], 0.9)
        self.assertEqual(stats["deduped"], 1)

    def test_gutter_column_pair_survives(self) -> None:
        dets = [
            {"label": "column", "bbox": [0, 0, 102, 500], "confidence": 0.9},
            {"label": "column", "bbox": [98, 0, 200, 500], "confidence": 0.9},
        ]
        self.assertEqual(len(postprocess_dets(dets)), 2)

    def test_single_page_number_kept(self) -> None:
        dets = [
            {
                "label": "page_number",
                "bbox": [0, 0, 10, 10],
                "confidence": 0.5,
            },
            {
                "label": "page_number",
                "bbox": [500, 0, 510, 10],
                "confidence": 0.9,
            },
        ]
        out = postprocess_dets(dets)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["confidence"], 0.9)

    def test_column_clipped_at_footnote_block(self) -> None:
        dets = [
            {"label": "column", "bbox": [0, 0, 100, 900], "confidence": 0.9},
            {
                "label": "footnote_block",
                "bbox": [0, 700, 100, 900],
                "confidence": 0.9,
            },
        ]
        out = postprocess_dets(dets)
        col = next(d for d in out if d["label"] == "column")
        self.assertEqual(col["bbox"][3], 700)

    def test_footnote_block_widened_to_column_extent(self) -> None:
        dets = [
            {"label": "column", "bbox": [0, 0, 100, 500], "confidence": 0.9},
            {"label": "column", "bbox": [110, 0, 200, 500], "confidence": 0.9},
            {
                "label": "footnote_block",
                "bbox": [40, 600, 150, 700],
                "confidence": 0.9,
            },
        ]
        out = postprocess_dets(dets)
        fb = next(d for d in out if d["label"] == "footnote_block")
        self.assertEqual(fb["bbox"][0], 0)
        self.assertEqual(fb["bbox"][2], 200)


class ColumnsTest(unittest.TestCase):
    def test_covered_duplicate_column_dropped(self) -> None:
        cols = _columns(
            [
                {"bbox": [0, 0, 100, 500], "confidence": 0.9},
                {"bbox": [5, 5, 95, 490], "confidence": 0.8},
            ]
        )
        self.assertEqual(len(cols), 1)

    def test_two_columns_labeled_by_position(self) -> None:
        cols = _columns(
            [
                {"bbox": [900, 0, 1600, 500], "confidence": 0.9},
                {"bbox": [100, 0, 800, 500], "confidence": 0.9},
            ]
        )
        self.assertEqual([c["side"] for c in cols], ["L", "R"])


class ReadingOrderTest(unittest.TestCase):
    def test_page_number_then_columns_then_footnotes(self) -> None:
        blocks: list[dict] = [
            {"band": "footnote", "column": "L", "bbox": [0, 900, 10, 910]},
            {"band": "body", "column": "R", "bbox": [500, 10, 510, 20]},
            {"band": "body", "column": "L", "bbox": [0, 10, 10, 20]},
            {"band": "page_number", "column": None, "bbox": [0, 0, 5, 5]},
        ]
        ordered = reading_order(blocks)
        self.assertEqual(
            [b["band"] for b in ordered],
            ["page_number", "body", "body", "footnote"],
        )
        self.assertEqual(ordered[1]["column"], "L")

    def test_no_column_blocks_order_by_position(self) -> None:
        blocks = [
            {"band": "body", "column": None, "bbox": [0, 500, 10, 510]},
            {"band": "body", "column": None, "bbox": [0, 100, 10, 110]},
        ]
        ordered = reading_order(blocks)
        self.assertEqual(ordered[0]["bbox"][1], 100)
