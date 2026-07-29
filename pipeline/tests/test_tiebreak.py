"""LightOn crop-tiebreak tests: expanding-anchor localization, the
gates, the degeneration guards and the one retry, the decision matrix,
provenance block lookup (wraps crop both bboxes), and the tiebreak
funnel metrics. The direct three-way vote is test_vote.py; machinery
both variants share is test_compare.py."""

from __future__ import annotations

from collections.abc import Sequence
from unittest import TestCase

from pipeline.core import compare, tiebreak
from pipeline.tests.factories import (
    BBOX,
    blocks_for,
    norm_rec,
    recon_rec,
    toks,
)


class CropCache:
    """A fake cache: reads by exact bbox, remembering every request.
    `retries` holds the second, re-decoded read of the same crop."""

    def __init__(
        self,
        reads: dict[int, str],
        retries: dict[int, str] | None = None,
    ) -> None:
        self.reads = {tuple(BBOX[i]): text for i, text in reads.items()}
        self.retries = {
            tuple(BBOX[i]): text for i, text in (retries or {}).items()
        }
        self.requested: list[tuple[int, ...]] = []

    def __call__(self, bbox: Sequence[int], retry: bool = False) -> str | None:
        key = tuple(int(c) for c in bbox)
        self.requested.append(key)
        return (self.retries if retry else self.reads).get(key)


class LocateTest(TestCase):
    """Expanding anchors — the one localization mechanism."""

    def test_unique_at_start_width(self) -> None:
        hay = "the quick brawn fox jumps".split()
        span = tiebreak.locate(hay, ["the", "quick"], ["fox", "jumps"])
        self.assertEqual(span, (2, 3))

    def test_empty_sides_anchor_to_the_edges(self) -> None:
        hay = "brawn fox".split()
        self.assertEqual(tiebreak.locate(hay, [], ["fox"]), (0, 1))
        self.assertEqual(tiebreak.locate(hay, ["brawn"], []), (1, 2))
        self.assertEqual(tiebreak.locate(hay, [], []), (0, len(hay)))

    def test_anchors_widen_until_unique(self) -> None:
        # "a b" recurs; only the third unit of context disambiguates
        hay = "x a b one y a b two".split()
        span = tiebreak.locate(hay, ["y", "a", "b"], [])
        self.assertEqual(span, (7, 8))

    def test_ambiguous_at_full_context_is_none(self) -> None:
        hay = "a b one a b two".split()
        self.assertIsNone(tiebreak.locate(hay, ["a", "b"], []))

    def test_missing_anchor_is_none(self) -> None:
        hay = "one two three".split()
        self.assertIsNone(tiebreak.locate(hay, ["four", "five"], ["six"]))

    def test_insertion_between_adjacent_anchors(self) -> None:
        hay = "one two zzz three four".split()
        span = tiebreak.locate(hay, ["one", "two"], ["three", "four"])
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
        retries: dict[int, str] | None = None,
    ) -> tuple[dict, CropCache]:
        cache = CropCache(reads or {}, retries)
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

    RAMBLE = "the quick brawn fox jumps jumps jumps"

    def test_ending_disagreement_rejects_the_read(self) -> None:
        # decoder degeneration: the read reaches NEITHER stream's
        # ending, and the retry says the same -> terminal
        rec, _ = self._run({0: self.RAMBLE}, retries={0: self.RAMBLE})
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "read-rejected")
        self.assertTrue(d["low_confidence"])
        self.assertFalse(d["tiebreak"]["accepted"])
        self.assertEqual(d["tiebreak"]["attempt"], 2)
        self.assertIn("supp", d["tiebreak"]["tails"])

    def test_rejected_read_waits_for_its_retry(self) -> None:
        """A read that fails a guard is not the end of the story: the
        dispute is pending ONE re-decoded read of the same crop, and
        keeps the main reading (low-confidence) until it arrives."""
        rec, _ = self._run({0: self.RAMBLE})
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "retry-pending")
        self.assertEqual(d["verdict"], "main")
        self.assertTrue(d["low_confidence"])
        self.assertTrue(d["tiebreak"]["retry_pending"])
        self.assertEqual(d["tiebreak"]["attempt"], 1)

    def test_retry_read_rescues_the_vote(self) -> None:
        """The retry is a real second chance: when its read survives the
        guards the dispute resolves on it."""
        rec, _ = self._run({0: self.RAMBLE}, retries={0: self.SUPP})
        (d,) = rec["disputes"]
        self.assertEqual(d["resolution"], "majority")
        self.assertEqual(d["verdict"], "supp1")
        self.assertFalse(d["low_confidence"])
        tb = d["tiebreak"]
        self.assertEqual(tb["attempt"], 2)
        self.assertTrue(tb["accepted"])
        self.assertFalse(tb["retry_pending"])

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
        # — and the retry reads it the same way
        unlocatable = "thequick brawn fox jumps over"
        rec, _ = self._run({0: unlocatable}, retries={0: unlocatable})
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

    def test_gate_is_per_block_not_per_dispute(self) -> None:
        """A dispute spanning a substantial block AND a one-glyph block
        is not cropped: the tiny block's crop is a few pixels, which the
        reader answers with invented content. The summed content clears
        the gate, so only a per-block test catches it."""
        main = toks("the quick brown fox jumps over") + toks("*", item=1)
        supp = toks("the quick brawn fox jumps over") + toks("+", item=1)
        cache = CropCache({0: self.MAIN, 1: "*"})
        rec = compare.compare_streams(
            norm_rec(main, ("mistral", "block", supp)),
            recon_rec([(0, "content", self.MAIN), (1, "content", "*")]),
            blocks_for([0, 1]),
            "lighton",
            cache,
        )
        reasons = [d["reason"] for d in rec["disputes"]]
        self.assertIn("under-gate", reasons)
        # the one-glyph block was never sent to the reader
        self.assertNotIn(tuple(BBOX[1]), cache.requested)

    def test_implausibly_long_read_is_discarded(self) -> None:
        """Decoder degeneration: a read many times longer than either
        engine's reading of the same blocks is not a reading, even when
        its ending happens to agree."""
        runaway = self.MAIN + " " + " ".join(["lorem ipsum dolor"] * 12)
        rec, _ = self._run({0: runaway}, retries={0: runaway})
        (d,) = rec["disputes"]
        self.assertEqual(d["reason"], "read-implausible")
        self.assertTrue(d["low_confidence"])
        self.assertEqual(d["verdict"], "main")
        tb = d["tiebreak"]
        self.assertTrue(tb["implausible"])
        self.assertFalse(tb["accepted"])
        self.assertGreater(tb["length_ratio"], tiebreak.READ_LEN_RATIO)

    def test_plausible_length_still_votes(self) -> None:
        """The guard leaves normal reads alone — a read the same length
        as the block still arbitrates, and records its ratio."""
        rec, _ = self._run({0: self.SUPP})
        (d,) = rec["disputes"]
        self.assertEqual(d["resolution"], "majority")
        self.assertLess(d["tiebreak"]["length_ratio"], 1.5)

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

    # a disputed run longer than HIGH_RISK_UNITS units on some side
    LONG = " ".join(f"w{i}" for i in range(compare.HIGH_RISK_UNITS + 5))

    def test_high_risk_needs_low_confidence_and_length(self) -> None:
        """Unresolved AND a disputed span over the unit threshold. The
        measure is UNITS of reading at stake, not how wide the text is
        set — so a whole clause qualifies where a changed word does
        not."""
        rec, _ = self._run(
            {},
            main=f"the quick {self.LONG} fox jumps over",
            supp="the quick fox jumps over",
            block_text=f"the quick {self.LONG} fox jumps over",
        )
        (d,) = rec["disputes"]
        i1, i2 = d["main_span"]
        self.assertGreater(i2 - i1, compare.HIGH_RISK_UNITS)
        self.assertTrue(d["low_confidence"])  # no cached read
        self.assertTrue(d["high_risk"])
        self.assertEqual(rec["metrics"]["n_high_risk"], 1)
        self.assertEqual(
            rec["params"]["high_risk_units"], compare.HIGH_RISK_UNITS
        )

    def test_short_or_resolved_disputes_are_not_high_risk(self) -> None:
        # unresolved but a single changed word — under the threshold
        rec, _ = self._run({})
        (d,) = rec["disputes"]
        self.assertTrue(d["low_confidence"])
        self.assertFalse(d["high_risk"])
        # a long span is NOT high risk once a majority resolves it
        long_main = f"the quick {self.LONG} fox jumps over"
        rec, _ = self._run(
            {0: "the quick fox jumps over"},
            main=long_main,
            supp="the quick fox jumps over",
            block_text=long_main,
        )
        (d,) = rec["disputes"]
        self.assertFalse(d["low_confidence"])
        self.assertFalse(d["high_risk"])
        self.assertEqual(rec["metrics"]["n_high_risk"], 0)

    def test_long_insertion_is_high_risk_via_the_supplemental(self) -> None:
        # main side is EMPTY (an insertion) — the supplemental's long
        # unresolved claim is what makes it risky, and it is measured on
        # that side's own aligned span
        rec, _ = self._run(
            {},
            main="alpha beta",
            supp=f"alpha {self.LONG} beta",
            block_text="alpha beta words here",
        )
        (d,) = rec["disputes"]
        self.assertEqual(d["reads"]["main"], "")
        self.assertEqual(d["main_span"][0], d["main_span"][1])  # zero-width
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
