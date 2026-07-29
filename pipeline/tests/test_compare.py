"""Compare + resolve tests: expanding-anchor localization, the
tiebreak decision matrix, the direct three-way vote (merged intervals +
projection), provenance block lookup (wraps crop both bboxes), degraded
streams, and the marks flow."""

from __future__ import annotations

from collections.abc import Sequence
from unittest import TestCase

from pipeline.core import compare

BBOX = {0: [10, 10, 200, 60], 1: [10, 70, 200, 120], 2: [10, 130, 200, 180]}


def toks(text: str, item: int = 0, **extra: object) -> list[dict]:
    """One token per whitespace word (keys == display). Spans are the
    words' real char offsets, like the tokenizer's — whitespace-
    separated words never touch, so none of them GLUE at assembly."""
    out = []
    pos = 0
    for w in text.split():
        start = text.index(w, pos)
        pos = start + len(w)
        t: dict = {
            "display": w,
            "key": w,
            "item": item,
            "span": [start, pos],
        }
        t.update(extra)
        out.append(t)
    return out


def norm_rec(main: list[dict], *supps: tuple[str, str, list[dict]]) -> dict:
    return {
        "main": {"engine": "dots", "tokens": main},
        "supplementals": [
            {"engine": e, "unit": u, "tokens": t} for e, u, t in supps
        ],
    }


def recon_rec(items: list[tuple[int, str, str]]) -> dict:
    return {
        "main": {
            "items": [
                {"id": i, "role": role, "text": text, "html": text}
                for i, role, text in items
            ]
        }
    }


def blocks_for(ids: Sequence[int]) -> list[dict]:
    return [{"id": i, "bbox": BBOX[i]} for i in ids]


class CropCache:
    """A fake cache: reads by exact bbox, remembering every request."""

    def __init__(self, reads: dict[int, str]) -> None:
        self.reads = {tuple(BBOX[i]): text for i, text in reads.items()}
        self.requested: list[tuple[int, ...]] = []

    def __call__(self, bbox: Sequence[int]) -> str | None:
        key = tuple(int(c) for c in bbox)
        self.requested.append(key)
        return self.reads.get(key)


class LocateTest(TestCase):
    """Expanding anchors — the one localization mechanism."""

    def test_unique_at_start_width(self) -> None:
        hay = "the quick brawn fox jumps".split()
        span = compare.locate(hay, ["the", "quick"], ["fox", "jumps"])
        self.assertEqual(span, (2, 3))

    def test_empty_sides_anchor_to_the_edges(self) -> None:
        hay = "brawn fox".split()
        self.assertEqual(compare.locate(hay, [], ["fox"]), (0, 1))
        self.assertEqual(compare.locate(hay, ["brawn"], []), (1, 2))
        self.assertEqual(compare.locate(hay, [], []), (0, len(hay)))

    def test_anchors_widen_until_unique(self) -> None:
        # "a b" recurs; only the third unit of context disambiguates
        hay = "x a b one y a b two".split()
        span = compare.locate(hay, ["y", "a", "b"], [])
        self.assertEqual(span, (7, 8))

    def test_ambiguous_at_full_context_is_none(self) -> None:
        hay = "a b one a b two".split()
        self.assertIsNone(compare.locate(hay, ["a", "b"], []))

    def test_missing_anchor_is_none(self) -> None:
        hay = "one two three".split()
        self.assertIsNone(compare.locate(hay, ["four", "five"], ["six"]))

    def test_insertion_between_adjacent_anchors(self) -> None:
        hay = "one two zzz three four".split()
        span = compare.locate(hay, ["one", "two"], ["three", "four"])
        self.assertEqual(span, (2, 3))


class TiebreakRouteTest(TestCase):
    """The dispute pipeline with the LightOn cached-crop tiebreak."""

    MAIN = "the quick brown fox jumps over"
    SUPP = "the quick brawn fox jumps over"

    def _run(
        self,
        reads: dict[int, str] | None,
        main: str = MAIN,
        supp: str = SUPP,
        role: str = "content",
        block_text: str | None = None,
    ) -> tuple[dict, CropCache]:
        cache = CropCache(reads or {})
        rec = compare.compare_streams(
            norm_rec(toks(main), ("mistral", "block", toks(supp))),
            recon_rec(
                [(0, role, block_text if block_text is not None else main)]
            ),
            blocks_for([0]),
            "lighton",
            cache,
        )
        return rec, cache

    def test_identical_streams_have_no_disputes(self) -> None:
        rec, cache = self._run({}, supp=self.MAIN)
        self.assertEqual(rec["disputes"], [])
        self.assertEqual(rec["metrics"]["n_disputes"], 0)
        self.assertEqual(cache.requested, [])

    def test_empty_vs_empty_is_agreement(self) -> None:
        rec = compare.compare_streams(
            norm_rec([], ("mistral", "block", [])),
            recon_rec([]),
            [],
            "lighton",
            None,
        )
        self.assertEqual(rec["disputes"], [])
        self.assertEqual(rec["degraded"], [])

    def test_supplemental_majority_adopts_the_supplemental(self) -> None:
        rec, _ = self._run({0: self.SUPP})
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "supp1")
        self.assertEqual(d["resolution"], "majority")
        self.assertFalse(d["low_confidence"])
        self.assertEqual(d["reads"]["lighton"], "brawn")
        self.assertEqual(d["displays"]["supp1"], "brawn")

    def test_main_majority_keeps_main(self) -> None:
        rec, _ = self._run({0: self.MAIN})
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["resolution"], "majority")
        self.assertFalse(d["low_confidence"])

    def test_three_way_split_falls_back_low_confidence(self) -> None:
        rec, _ = self._run({0: "the quick brwn fox jumps over"})
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["resolution"], "fallback")
        self.assertEqual(d["reason"], "three-way-split")
        self.assertTrue(d["low_confidence"])

    def test_missing_cached_read_falls_back(self) -> None:
        rec, cache = self._run({})
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "no-cached-read")
        self.assertTrue(d["low_confidence"])
        self.assertEqual(cache.requested, [tuple(BBOX[0])])

    def test_ending_disagreement_rejects_the_read(self) -> None:
        # decoder degeneration: the read reaches NEITHER stream's ending
        rec, _ = self._run({0: "the quick brawn fox jumps jumps jumps"})
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "read-rejected")
        self.assertTrue(d["low_confidence"])
        self.assertFalse(d["tiebreak"]["accepted"])
        self.assertIn("supp", d["tiebreak"]["tails"])

    def test_tail_matching_the_supplemental_accepts(self) -> None:
        # the block's own ending IS the dispute: the read can never
        # match main's tail, but it matches the supplemental's —
        # accepted (either side) and voted
        rec, _ = self._run(
            {0: "the quick brawn"},
            main="the quick brown",
            supp="the quick brawn",
            block_text="the quick brown",
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["tiebreak"]["accepted_by"], "supp1")
        self.assertEqual(d["verdict"], "supp1")
        self.assertEqual(d["resolution"], "majority")
        self.assertFalse(d["low_confidence"])

    def test_unlocatable_dispute_falls_back(self) -> None:
        # tail agrees, but the pre-anchor context is absent in the read
        rec, _ = self._run(
            {0: "thequick brawn fox jumps over"},
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "not-located")
        self.assertTrue(d["low_confidence"])
        self.assertTrue(d["tiebreak"]["accepted"])
        self.assertFalse(d["tiebreak"]["located"])

    def test_under_gate_never_crops(self) -> None:
        rec, cache = self._run(
            {0: "on"}, main="on", supp="an", block_text="on"
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "under-gate")
        self.assertTrue(d["low_confidence"])
        self.assertEqual(cache.requested, [])

    def test_skip_roles_are_main_authoritative(self) -> None:
        rec, cache = self._run(
            {},
            main="page fifteen of",
            supp="page sixteen of",
            role="page_number",
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["resolution"], "authoritative")
        self.assertEqual(d["reason"], "role-authoritative")
        self.assertFalse(d["low_confidence"])
        self.assertEqual(cache.requested, [])

    def test_wrapped_dispute_crops_both_bboxes(self) -> None:
        main = (
            toks("start", item=0)
            + [
                {
                    "display": "wrapjoined",
                    "key": "wrapjoined",
                    "item": 0,
                    "span": [6, 12],
                    "wrap": [1, 0, 6],
                }
            ]
            + toks("tail words here", item=1)
        )
        supp = toks("start wrapjoyned tail words here")
        cache = CropCache({0: "start wrapjoined", 1: "tail words here"})
        rec = compare.compare_streams(
            norm_rec(main, ("mistral", "block", supp)),
            recon_rec(
                [
                    (0, "content", "start wrapjo-"),
                    (1, "content", "joined tail words here"),
                ]
            ),
            blocks_for([0, 1]),
            "lighton",
            cache,
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["blocks"], [0, 1])
        self.assertEqual(d["tiebreak"]["bboxes"], [BBOX[0], BBOX[1]])
        # both crops were read, joined, and LightOn sided with main
        self.assertEqual(d["verdict"], "main")
        self.assertEqual(d["resolution"], "majority")

    def test_insertion_anchors_to_the_preceding_block(self) -> None:
        main = toks("alpha beta", item=0) + toks("gamma delta", item=1)
        supp = toks("alpha beta extra gamma delta")
        rec = compare.compare_streams(
            norm_rec(main, ("mistral", "block", supp)),
            recon_rec(
                [
                    (0, "content", "alpha beta words"),
                    (1, "content", "gamma delta words"),
                ]
            ),
            blocks_for([0, 1]),
            "lighton",
            CropCache({}),
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["main_span"], [2, 2])
        self.assertEqual(d["blocks"], [0])

    def test_degraded_supplemental_reports_no_disputes(self) -> None:
        rec = compare.compare_streams(
            norm_rec(toks(self.MAIN), ("gemini", "page_xml", [])),
            recon_rec([(0, "content", self.MAIN)]),
            blocks_for([0]),
            "lighton",
            None,
        )
        self.assertEqual(rec["degraded"], ["supp1"])
        self.assertEqual(rec["disputes"], [])

    def test_high_risk_needs_low_confidence_and_length(self) -> None:
        # unresolved AND the disputed text exceeds 5 chars -> high risk
        rec, _ = self._run(
            {},
            main="the quick brownish fox jumps over",
            supp="the quick brawnish fox jumps over",
            block_text="the quick brownish fox jumps over",
        )
        (d,) = rec["disputes"]
        self.assertTrue(d["low_confidence"])  # no cached read
        self.assertTrue(d["high_risk"])  # "brownish" is 8 chars
        self.assertEqual(rec["metrics"]["n_high_risk"], 1)
        self.assertEqual(rec["params"]["high_risk_chars"], 5)

    def test_short_or_resolved_disputes_are_not_high_risk(self) -> None:
        # unresolved but short ("brown" = 5 chars, not > 5)
        rec, _ = self._run({})
        (d,) = rec["disputes"]
        self.assertTrue(d["low_confidence"])
        self.assertFalse(d["high_risk"])
        # long but RESOLVED by majority
        rec, _ = self._run(
            {0: "the quick brawnish fox jumps over"},
            main="the quick brownish fox jumps over",
            supp="the quick brawnish fox jumps over",
            block_text="the quick brownish fox jumps over",
        )
        (d,) = rec["disputes"]
        self.assertFalse(d["low_confidence"])
        self.assertFalse(d["high_risk"])
        self.assertEqual(rec["metrics"]["n_high_risk"], 0)

    def test_long_insertion_is_high_risk_via_the_supplemental(self) -> None:
        # main side is EMPTY (an insertion) — the supplemental's long
        # unresolved claim is what makes it risky
        rec, _ = self._run(
            {},
            main="alpha beta",
            supp="alpha entirely new sentence beta",
            block_text="alpha beta words here",
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["reads"]["main"], "")
        self.assertTrue(d["high_risk"])

    def test_metrics_count_the_tiebreak_funnel(self) -> None:
        rec, _ = self._run({0: self.SUPP})
        m = rec["metrics"]
        self.assertEqual(m["n_disputes"], 1)
        self.assertEqual(m["disputed_units"], 1)
        self.assertEqual(m["by_resolution"], {"majority": 1})
        self.assertEqual(
            m["tiebreak"],
            {"attempted": 1, "cached": 1, "accepted": 1, "voted": 1},
        )
        self.assertEqual(rec["params"]["gate_min_chars"], 10)


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
