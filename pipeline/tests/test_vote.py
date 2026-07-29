"""Direct three-way vote tests: merged disagreement intervals,
projection out of each alignment, 2-of-3 majority, and the degraded
case with no third voter. The LightOn crop tiebreak is
test_tiebreak.py; machinery both variants share is test_compare.py."""

from __future__ import annotations

from unittest import TestCase

from pipeline.core import compare
from pipeline.tests.factories import blocks_for, norm_rec, recon_rec, toks


class VoteRouteTest(TestCase):
    """The direct three-way vote (no tiebreaker model)."""

    def _run(self, main: str, s1: str, s2: str) -> dict:
        return compare.compare_streams(
            norm_rec(
                toks(main),
                ("mistral", "block", toks(s1)),
                ("surya", "block", toks(s2)),
            ),
            recon_rec([(0, "content", main)]),
            blocks_for([0]),
            None,
            None,
        )

    def test_supplementals_outvote_main(self) -> None:
        rec = self._run("a brown fox", "a brawn fox", "a brawn fox")
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "supp1")
        self.assertEqual(d["resolution"], "majority")
        self.assertEqual(d["reads"]["supp2"], "brawn")

    def test_one_supplemental_backs_main(self) -> None:
        rec = self._run("a brown fox", "a brawn fox", "a brown fox")
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["resolution"], "majority")

    def test_three_way_split_keeps_main_low_confidence(self) -> None:
        rec = self._run("a brown fox", "a brawn fox", "a brwn fox")
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["reason"], "three-way-split")
        self.assertTrue(d["low_confidence"])

    def test_touching_regions_merge_into_one_interval(self) -> None:
        # supp1 disputes unit b, supp2 disputes unit c: one interval
        rec = self._run("a b c d", "a x c d", "a b y d")
        (d,) = rec["disputes"]
        self.assertEqual(d["main_span"], [1, 3])
        self.assertEqual(d["reads"]["main"], "b c")
        self.assertEqual(d["reads"]["supp1"], "x c")
        self.assertEqual(d["reads"]["supp2"], "b y")
        self.assertEqual(d["reason"], "three-way-split")

    def test_insertion_merges_with_a_touching_replace(self) -> None:
        # supp1 inserts z after b; supp2 rewrites b — one interval
        rec = self._run("a b c", "a b z c", "a q c")
        (d,) = rec["disputes"]
        self.assertEqual(d["main_span"], [1, 2])
        self.assertEqual(d["reads"]["supp1"], "b z")
        self.assertEqual(d["reads"]["supp2"], "q")

    def test_agreeing_insertions_are_adopted(self) -> None:
        rec = self._run("a c", "a b c", "a b c")
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "supp1")
        self.assertEqual(d["displays"]["supp1"], "b")

    def test_degraded_supplemental_leaves_no_third_voter(self) -> None:
        rec = compare.compare_streams(
            norm_rec(
                toks("a brown fox"),
                ("gemini", "page_xml", []),
                ("surya", "block", toks("a brawn fox")),
            ),
            recon_rec([(0, "content", "a brown fox")]),
            blocks_for([0]),
            None,
            None,
        )
        self.assertEqual(rec["degraded"], ["supp1"])
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["reason"], "no-vote")
        self.assertTrue(d["low_confidence"])
        self.assertIn("supp2", d["spans"])
        self.assertNotIn("supp1", d["spans"])
