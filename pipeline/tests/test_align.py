"""Cross-engine bbox alignment."""

from django.test import SimpleTestCase

from pipeline.core import align


def block(bid: int, bbox: list[float], text: str, label: str = "Text") -> dict:
    return {
        "id": bid,
        "bbox": bbox,
        "label": label,
        "text": text,
        "styled": text,
    }


class GeometryTests(SimpleTestCase):
    def test_containment_scores_against_the_smaller_box(self) -> None:
        big = [0.0, 0.0, 100.0, 100.0]
        small = [10.0, 10.0, 30.0, 30.0]
        self.assertEqual(align.contained(big, small), 1.0)
        self.assertAlmostEqual(align.iou(big, small), 0.04)

    def test_disjoint_boxes_do_not_link(self) -> None:
        self.assertEqual(
            align.contained([0.0, 0.0, 10.0, 10.0], [50.0, 50.0, 60.0, 60.0]),
            0.0,
        )

    def test_plain_strips_markup_and_image_placeholders(self) -> None:
        self.assertEqual(align.plain("<em>Ex</em>  parte"), "Ex parte")
        self.assertEqual(align.plain("![img-0.jpeg](img-0.jpeg)"), "")
        self.assertEqual(align.plain("a &amp; b"), "a & b")
        self.assertEqual(align.plain(None), "")


class AlignPageTests(SimpleTestCase):
    def test_one_box_against_three_becomes_one_group(self) -> None:
        """The whole point: engines split a region differently, and a
        naive 1:1 match would throw the region away."""
        groups = align.align_page(
            {
                "dots": [block(0, [100.0, 100.0, 800.0, 400.0], "a b c")],
                "surya": [
                    block(0, [100.0, 100.0, 800.0, 200.0], "a"),
                    block(1, [100.0, 200.0, 800.0, 300.0], "b"),
                    block(2, [100.0, 300.0, 800.0, 400.0], "c"),
                ],
            }
        )
        self.assertEqual(len(groups), 1)
        g = groups[0]
        self.assertEqual(g["present"], ["dots", "surya"])
        self.assertEqual(g["engines"]["surya"]["n_boxes"], 3)
        self.assertEqual(g["engines"]["surya"]["text"], "a b c")
        self.assertEqual(g["engines"]["dots"]["text"], "a b c")

    def test_group_members_concatenate_in_reading_order(self) -> None:
        groups = align.align_page(
            {
                "dots": [block(0, [100.0, 100.0, 800.0, 400.0], "one two")],
                "surya": [
                    block(1, [100.0, 250.0, 800.0, 400.0], "two"),
                    block(0, [100.0, 100.0, 800.0, 240.0], "one"),
                ],
            }
        )
        self.assertEqual(groups[0]["engines"]["surya"]["text"], "one two")

    def test_separate_regions_stay_separate(self) -> None:
        groups = align.align_page(
            {
                "dots": [
                    block(0, [100.0, 100.0, 800.0, 400.0], "top"),
                    block(1, [100.0, 900.0, 800.0, 1200.0], "bottom"),
                ],
                "surya": [
                    block(0, [100.0, 100.0, 800.0, 400.0], "top"),
                    block(1, [100.0, 900.0, 800.0, 1200.0], "bottom"),
                ],
            }
        )
        self.assertEqual(len(groups), 2)

    def test_page_scale_region_does_not_chain_the_page(self) -> None:
        """A whole-page picture box overlaps everything; letting it link
        would collapse the page into one group."""
        groups = align.align_page(
            {
                "dots": [
                    block(0, [100.0, 100.0, 800.0, 400.0], "top"),
                    block(1, [100.0, 900.0, 800.0, 1200.0], "bottom"),
                ],
                "surya": [
                    block(
                        0,
                        [0.0, 0.0, 1700.0, 2200.0],
                        "",
                        label="Picture",
                    )
                ],
            }
        )
        self.assertEqual(len(groups), 3)
        self.assertEqual(sum(1 for g in groups if g["page_scale"]), 1)

    def test_same_engine_boxes_never_link_to_each_other(self) -> None:
        groups = align.align_page(
            {
                "dots": [
                    block(0, [100.0, 100.0, 800.0, 400.0], "a"),
                    block(1, [120.0, 120.0, 780.0, 380.0], "b"),
                ]
            }
        )
        self.assertEqual(len(groups), 2)

    def test_alignment_iou_flags_a_permissive_link(self) -> None:
        """Linking is by containment, so a small box inside a big one
        joins — and the merged-box IoU says the merge is dubious."""
        groups = align.align_page(
            {
                "dots": [block(0, [100.0, 100.0, 900.0, 900.0], "long")],
                "surya": [block(0, [400.0, 400.0, 500.0, 500.0], "x")],
            }
        )
        self.assertEqual(len(groups), 1)
        self.assertLess(groups[0]["alignment_iou"], 0.3)

    def test_single_engine_group_scores_a_full_iou(self) -> None:
        groups = align.align_page(
            {"dots": [block(0, [100.0, 100.0, 800.0, 400.0], "only")]}
        )
        self.assertEqual(groups[0]["alignment_iou"], 1.0)
        self.assertEqual(groups[0]["present"], ["dots"])

    def test_blocks_without_a_bbox_are_dropped(self) -> None:
        groups = align.align_page(
            {
                "mistral": [
                    {"id": 0, "bbox": None, "type": "text", "text": "x"},
                    block(1, [100.0, 100.0, 800.0, 400.0], "kept"),
                ]
            }
        )
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["engines"]["mistral"]["text"], "kept")

    def test_inverted_bbox_is_normalized(self) -> None:
        groups = align.align_page(
            {"dots": [block(0, [800.0, 400.0, 100.0, 100.0], "flipped")]}
        )
        self.assertEqual(groups[0]["bbox"], [100.0, 100.0, 800.0, 400.0])

    def test_group_bbox_unions_every_engine(self) -> None:
        groups = align.align_page(
            {
                "dots": [block(0, [100.0, 100.0, 800.0, 400.0], "a")],
                "surya": [block(0, [120.0, 90.0, 850.0, 380.0], "a")],
            }
        )
        self.assertEqual(groups[0]["bbox"], [100.0, 90.0, 850.0, 400.0])
