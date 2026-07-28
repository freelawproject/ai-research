import unittest

from pipeline.core.reconstruct import (
    reconstruct_blocks,
    reconstruct_gemini,
)

_CONTAINERS = {
    "by_label": {
        "page_number": [{"bbox": [800, 50, 900, 90]}],
        "footnote_block": [{"bbox": [100, 1800, 1600, 2100]}],
        "column": [
            {"bbox": [100, 100, 800, 1750]},
            {"bbox": [900, 100, 1600, 1750]},
        ],
    },
    "columns": [
        {"side": "L", "bbox": [100, 100, 800, 1750]},
        {"side": "R", "bbox": [900, 100, 1600, 1750]},
    ],
}


def _block(bid: int, bbox: list[float], text: str = "", **kw: str) -> dict:
    return {"id": bid, "bbox": bbox, "text": text or f"block {bid}", **kw}


class ReconstructBlocksTest(unittest.TestCase):
    def test_reading_order_and_bands(self) -> None:
        blocks = [
            _block(0, [150, 1850, 700, 1950], "a footnote"),
            _block(1, [950, 200, 1500, 300], "right column"),
            _block(2, [150, 200, 700, 300], "left column"),
            _block(3, [810, 55, 890, 85], "2612"),
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["order"], [3, 2, 1, 0])
        self.assertEqual(
            [i["role"] for i in r["items"]],
            ["page_number", "content", "content", "footnote"],
        )
        self.assertEqual(
            r["text"], "2612\nleft column\nright column\na footnote"
        )

    def test_image_block_keeps_position_contributes_no_text(self) -> None:
        blocks = [
            _block(0, [150, 200, 700, 300], "before"),
            _block(1, [150, 400, 700, 900], "IN-IMAGE JUNK", label="Picture"),
            _block(2, [150, 1000, 700, 1100], "after"),
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["order"], [0, 1, 2])
        self.assertEqual(r["items"][1]["role"], "image")
        self.assertEqual(r["items"][1]["html"], "")
        self.assertEqual(r["text"], "before\nafter")

    def test_mistral_image_type_treated_as_image(self) -> None:
        blocks = [_block(0, [150, 200, 700, 800], "![img](x)", type="image")]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["items"][0]["role"], "image")
        self.assertEqual(r["text"], "")

    def test_bboxless_block_reported_not_lost(self) -> None:
        blocks = [
            _block(0, [150, 200, 700, 300], "placed"),
            {"id": 1, "bbox": None, "text": "no bbox"},
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["order"], [0])
        self.assertEqual(r["no_bbox"], [1])

    def test_items_carry_styled_html(self) -> None:
        blocks = [
            _block(
                0,
                [150, 200, 700, 300],
                "see *Searle* now",
                styled="see <em>Searle</em> now",
            ),
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["items"][0]["html"], "see <em>Searle</em> now")

    def test_items_carry_raw_text_for_normalization(self) -> None:
        blocks = [
            _block(0, [150, 200, 700, 300], "see *Searle* now"),
            _block(1, [150, 400, 700, 900], "JUNK", label="Picture"),
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["items"][0]["text"], "see *Searle* now")
        self.assertEqual(r["items"][1]["text"], "")  # image item

    def test_near_black_image_block_dropped_as_redaction(self) -> None:
        blocks = [
            _block(0, [150, 200, 700, 300], "before"),
            {
                "id": 1,
                "bbox": [150, 400, 700, 900],
                "text": "",
                "label": "Picture",
                "black_frac": 0.91,  # redaction placeholder
            },
            {
                "id": 2,
                "bbox": [150, 1000, 700, 1400],
                "text": "",
                "label": "Picture",
                "black_frac": 0.31,  # a real figure
            },
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["redactions"], [1])
        self.assertEqual(r["order"], [0, 2])
        self.assertEqual(r["items"][1]["role"], "image")

    def test_near_black_text_block_dropped_as_hallucination(self) -> None:
        # mistral invents whole passages over redacted regions — a
        # near-black TEXT block is redaction territory too
        blocks = [
            _block(0, [150, 200, 700, 300], "real text"),
            {
                "id": 1,
                "bbox": [150, 400, 700, 900],
                "text": "The court of the United States is a legal entity.",
                "type": "text",
                "black_frac": 0.97,  # hallucinated over a redaction
            },
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks)
        self.assertEqual(r["redactions"], [1])
        self.assertEqual(r["order"], [0])
        self.assertEqual(r["text"], "real text")


class ReconstructGeminiTest(unittest.TestCase):
    def test_document_order_and_tag_roles(self) -> None:
        blocks = [
            {"id": 0, "tag": "pagenumber", "attrs": {}, "text": "58"},
            {"id": 1, "tag": "p", "attrs": {}, "text": "body"},
            {"id": 2, "tag": "footnote", "attrs": {}, "text": "note"},
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["order"], [0, 1, 2])
        self.assertEqual(
            [i["role"] for i in r["items"]],
            ["page_number", "content", "footnote"],
        )
        self.assertEqual(r["text"], "58\nbody\nnote")

    def test_coords_reorder_within_column(self) -> None:
        # document order is wrong (y=300 before y=100); coords fix it
        blocks = [
            {
                "id": 0,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "300"},
                "text": "second",
            },
            {
                "id": 1,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "100"},
                "text": "first",
            },
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["order"], [1, 0])
        self.assertEqual(r["text"], "first\nsecond")

    def test_left_column_reads_before_right(self) -> None:
        blocks = [
            {
                "id": 0,
                "tag": "p",
                "attrs": {"col": "R", "x": "200", "y": "100"},
                "text": "right",
            },
            {
                "id": 1,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "500"},
                "text": "left",
            },
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["text"], "left\nright")

    def test_unknown_tag_content_preserved(self) -> None:
        blocks = [
            {"id": 0, "tag": "author", "attrs": {}, "text": "Justice X"},
            {"id": 1, "tag": "p", "attrs": {}, "text": "body"},
        ]
        r = reconstruct_gemini(blocks)
        self.assertIn("Justice X", r["text"])

    def test_star_pages_placed_in_order_and_reported(self) -> None:
        blocks = [
            {
                "id": 0,
                "tag": "p",
                "attrs": {},
                "text": "body ★306 more",  # inline marker at position
                "star_pages": ["306"],
            },
            {
                "id": 1,
                "tag": "parallelpagenumber",
                "attrs": {},
                "text": "★307",  # block-level marker keeps doc position
                "star_pages": ["307"],
            },
            {"id": 2, "tag": "p", "attrs": {}, "text": "after"},
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["order"], [0, 1, 2])
        self.assertEqual(r["text"], "body ★306 more\n★307\nafter")
        self.assertEqual(r["star_pages"], ["306", "307"])

    def test_img_tag_text_omitted(self) -> None:
        blocks = [
            {"id": 0, "tag": "p", "attrs": {}, "text": "body"},
            {
                "id": 1,
                "tag": "img",
                "attrs": {"type": "scan"},
                "text": "caption junk",
            },
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["omitted"], [1])
        self.assertNotIn("caption junk", r["text"])

    def test_missing_col_inherits_predecessor_column(self) -> None:
        # coords present but no col attribute: the block stays in its
        # predecessor's column rather than falling to an "other" bucket
        blocks = [
            {
                "id": 0,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "100"},
                "text": "a",
            },
            {
                "id": 1,
                "tag": "p",
                "attrs": {"x": "10", "y": "150"},
                "text": "b",
            },
            {
                "id": 2,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "200"},
                "text": "c",
            },
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["text"], "a\nb\nc")

    def test_coordless_block_keeps_document_position(self) -> None:
        blocks = [
            {
                "id": 0,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "100"},
                "text": "a",
            },
            {"id": 1, "tag": "heading", "attrs": {}, "text": "b"},
            {
                "id": 2,
                "tag": "p",
                "attrs": {"col": "L", "x": "10", "y": "200"},
                "text": "c",
            },
        ]
        r = reconstruct_gemini(blocks)
        self.assertEqual(r["text"], "a\nb\nc")


class SupplementalBlockReconstructTest(unittest.TestCase):
    """Every block engine goes through the same supplemental path:
    reconstruct_blocks with the MAIN engine's blocks for the in-image
    check (surya block, mistral, dots — no special cases)."""

    def test_mistral_main_image_blocks_flag_in_image(self) -> None:
        # mistral marks image blocks with type= (not label=) — a
        # supplemental block inside one is in-image text
        main = [
            {"id": 0, "type": "text", "bbox": [100, 100, 800, 400]},
            {"id": 1, "type": "image", "bbox": [100, 500, 800, 1200]},
        ]
        blocks = [
            {
                "id": 0,
                "bbox": [200, 600, 700, 640],
                "label": "Text",
                "text": "caption",
                "styled": "caption",
            }
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks, main_blocks=main)
        self.assertEqual(r["in_image"], [0])
        self.assertEqual(r["text"], "")

    def test_block_inside_main_picture_is_in_image(self) -> None:
        main_blocks = [
            _block(0, [100, 500, 800, 1200], label="Picture"),
            _block(1, [100, 100, 800, 400], label="Text"),
        ]
        blocks = [
            {
                "id": 0,
                "bbox": [150, 150, 700, 350],
                "label": "Text",
                "text": "real",
                "styled": "real",
            },
            {
                "id": 1,
                "bbox": [200, 600, 700, 700],
                "label": "Text",
                "text": "caption on figure",
                "styled": "caption on figure",
            },
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks, main_blocks=main_blocks)
        self.assertEqual(r["in_image"], [1])
        self.assertEqual(r["text"], "real")

    def test_own_picture_block_becomes_image_item(self) -> None:
        blocks = [
            {
                "id": 0,
                "bbox": [150, 400, 700, 900],
                "label": "Picture",
                "text": "",
                "styled": "",
            },
        ]
        r = reconstruct_blocks(_CONTAINERS, blocks, main_blocks=[])
        self.assertEqual(r["items"][0]["role"], "image")
        self.assertEqual(r["in_image"], [])

    def test_main_use_reports_no_in_image(self) -> None:
        r = reconstruct_blocks(
            _CONTAINERS, [_block(0, [150, 200, 700, 300], "text")]
        )
        self.assertNotIn("in_image", r)
