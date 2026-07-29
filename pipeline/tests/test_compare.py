"""Compare-stage tests for what every route shares: reorder pairing
(both streams carry the content, they disagree on placement) and the
marks flow (reported, never disputed). The two resolution variants have
their own files — test_tiebreak.py and test_vote.py."""

from __future__ import annotations

from unittest import TestCase

from pipeline.core import compare
from pipeline.tests.factories import blocks_for, norm_rec, recon_rec, toks


class ReorderTest(TestCase):
    """Paired deletion + insertion of near-identical text = a REORDER:
    both streams carry the content, they disagree on where it belongs."""

    MOVED = (
        "existed instead it raised this point as part of an assertion "
        "that they were attempting to avoid liability"
    )
    # longer than MOVED, so the aligner anchors on it and the moved
    # passage becomes the deletion/insertion pair
    BODY = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa mu "
        "nu xi omicron pi rho sigma tau upsilon phi chi psi omega one"
    )

    def _run(self, main: str, supp: str) -> dict:
        return compare.compare_streams(
            norm_rec(toks(main), ("gemini", "page_xml", toks(supp))),
            recon_rec([(0, "content", main)]),
            blocks_for([0]),
            "lighton",
            None,  # no crop reads: unresolved unless paired as reorder
        )

    def test_moved_block_pairs_into_a_reorder(self) -> None:
        rec = self._run(
            f"{self.MOVED} {self.BODY}",
            f"{self.BODY} {self.MOVED}",
        )
        self.assertEqual(len(rec["disputes"]), 2)
        for d in rec["disputes"]:
            self.assertEqual(d["resolution"], "reorder")
            self.assertEqual(d["verdict"], "main")
            self.assertFalse(d["low_confidence"])
            self.assertFalse(d["high_risk"])
        self.assertEqual(rec["metrics"]["by_resolution"], {"reorder": 2})
        self.assertEqual(rec["metrics"]["n_low_confidence"], 0)
        self.assertEqual(rec["params"]["reorder_sim"], 0.9)

    def test_near_identical_tolerates_edge_differences(self) -> None:
        # the moved copy carries a leading footnote label the original
        # lacks (the a3d.340.1__p3 case) — still one reorder
        rec = self._run(
            f"{self.MOVED} {self.BODY}",
            f"{self.BODY} 3 . {self.MOVED}",
        )
        self.assertEqual(
            [d["resolution"] for d in rec["disputes"]],
            ["reorder", "reorder"],
        )

    def test_different_content_does_not_pair(self) -> None:
        rec = self._run(
            "one two three four five six alpha beta gamma",
            "alpha beta gamma cat dog bird fish cow hen",
        )
        for d in rec["disputes"]:
            self.assertNotEqual(d["resolution"], "reorder")
            self.assertTrue(d["low_confidence"])

    def test_short_spans_never_pair(self) -> None:
        # under REORDER_MIN_UNITS on both sides: not a reorder
        rec = self._run(f"x y {self.BODY}", f"{self.BODY} x y")
        for d in rec["disputes"]:
            self.assertNotEqual(d["resolution"], "reorder")


class MarksFlowTest(TestCase):
    """Footnote marks are compared in their own flow, never as text."""

    def test_matching_mark_sequences_agree(self) -> None:
        main = toks("word one") + toks("two", marks=["4"])
        supp = toks("word one") + toks("two", marks=["4"])
        rec = compare.compare_streams(
            norm_rec(main, ("mistral", "block", supp)),
            recon_rec([(0, "content", "word one two")]),
            blocks_for([0]),
            "lighton",
            None,
        )
        (m,) = rec["marks"]
        self.assertTrue(m["agree"])
        self.assertEqual(m["main"], ["4"])

    def test_mark_differences_are_reported_not_disputed(self) -> None:
        main = toks("word one") + toks("two", marks=["4"])
        supp = toks("word one two")
        rec = compare.compare_streams(
            norm_rec(main, ("mistral", "block", supp)),
            recon_rec([(0, "content", "word one two")]),
            blocks_for([0]),
            "lighton",
            None,
        )
        self.assertEqual(rec["disputes"], [])  # marks never dispute text
        (m,) = rec["marks"]
        self.assertFalse(m["agree"])
        self.assertEqual(m["diffs"], [{"main": ["4"], "supp": []}])
