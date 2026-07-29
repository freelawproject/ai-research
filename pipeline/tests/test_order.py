"""Reading order inferred from bbox geometry."""

from django.test import SimpleTestCase

from pipeline.core import order

# A two-column reporter page: left column at x0~230, right at x0~870,
# two running heads across the top.
HEAD_L = {"id": "hl", "bbox": [230.0, 118.0, 440.0, 152.0]}
HEAD_R = {"id": "hr", "bbox": [1240.0, 116.0, 1410.0, 150.0]}
L1 = {"id": "l1", "bbox": [230.0, 200.0, 810.0, 600.0]}
L2 = {"id": "l2", "bbox": [230.0, 620.0, 810.0, 1400.0]}
R1 = {"id": "r1", "bbox": [870.0, 200.0, 1460.0, 900.0]}
R2 = {"id": "r2", "bbox": [870.0, 920.0, 1460.0, 1800.0]}


def ids(placed: list[dict]) -> list[str]:
    return [p["id"] for p in placed]


class ColumnBoundaryTests(SimpleTestCase):
    def test_two_columns_split_at_the_right_column_edge(self) -> None:
        boxes = [HEAD_L, HEAD_R, L1, L2, R1, R2]
        self.assertEqual(order.column_boundary(boxes), 850.0)

    def test_single_column_has_no_boundary(self) -> None:
        boxes = [
            {"id": str(i), "bbox": [230.0, y, 1460.0, y + 200.0]}
            for i, y in enumerate((200.0, 420.0, 640.0, 860.0))
        ]
        self.assertIsNone(order.column_boundary(boxes))

    def test_running_heads_do_not_hide_the_gutter(self) -> None:
        """A centered head straddles the gutter; only body boxes vote."""
        wide_head = {"id": "hw", "bbox": [580.0, 118.0, 1140.0, 152.0]}
        boxes = [wide_head, L1, L2, R1, R2]
        self.assertEqual(order.column_boundary(boxes), 850.0)

    def test_too_few_boxes_to_call_it(self) -> None:
        self.assertIsNone(order.column_boundary([L1, R1]))


class PlaceTests(SimpleTestCase):
    def test_left_column_reads_before_right(self) -> None:
        placed = order.place([R2, L2, R1, L1])
        self.assertEqual(ids(placed), ["l1", "l2", "r1", "r2"])

    def test_heads_read_across_before_the_body(self) -> None:
        placed = order.place([R2, HEAD_R, L1, HEAD_L, R1, L2])
        self.assertEqual(ids(placed), ["hl", "hr", "l1", "l2", "r1", "r2"])

    def test_two_body_boxes_are_too_few_to_call_columns(self) -> None:
        """Below the vote threshold the page reads top-to-bottom rather
        than being split on one gap that might be a wide margin."""
        placed = order.place([R1, L1])
        self.assertIsNone(placed[0]["column"])

    def test_head_band_orders_by_x_not_by_a_pixel_of_y(self) -> None:
        """The two heads sit level; HEAD_R is 2px higher but reads
        second because it is further right."""
        self.assertEqual(ids(order.place([HEAD_R, HEAD_L]))[:2], ["hl", "hr"])

    def test_full_width_box_separates_the_columns_above_and_below(
        self,
    ) -> None:
        banner = {"id": "sep", "bbox": [230.0, 1450.0, 1460.0, 1520.0]}
        below_l = {"id": "bl", "bbox": [230.0, 1560.0, 810.0, 1900.0]}
        below_r = {"id": "br", "bbox": [870.0, 1560.0, 1460.0, 1900.0]}
        placed = order.place([L1, R1, banner, below_l, below_r])
        self.assertEqual(ids(placed), ["l1", "r1", "sep", "bl", "br"])

    def test_bands_and_columns_are_stamped(self) -> None:
        placed = order.place([HEAD_L, HEAD_R, L1, L2, R1, R2])
        by_id = {p["id"]: p for p in placed}
        self.assertEqual(by_id["hl"]["band"], "head")
        self.assertIsNone(by_id["hl"]["column"])
        self.assertEqual(by_id["l1"]["column"], "L")
        self.assertEqual(by_id["r1"]["column"], "R")
        self.assertEqual(by_id["r1"]["band"], "body")

    def test_single_column_page_reads_top_to_bottom(self) -> None:
        rows = [
            {"id": str(i), "bbox": [230.0, y, 1460.0, y + 200.0]}
            for i, y in enumerate((860.0, 200.0, 640.0, 420.0))
        ]
        self.assertEqual(ids(order.place(rows)), ["1", "3", "2", "0"])

    def test_no_box_is_lost_or_duplicated(self) -> None:
        boxes = [HEAD_L, HEAD_R, L1, L2, R1, R2]
        placed = order.place(boxes)
        self.assertEqual(sorted(ids(placed)), sorted(b["id"] for b in boxes))

    def test_input_is_not_mutated(self) -> None:
        box = {"id": "l1", "bbox": [230.0, 200.0, 810.0, 600.0]}
        order.place([box, L2, R1, R2])
        self.assertEqual(set(box), {"id", "bbox"})
