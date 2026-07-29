import unittest

from pipeline.core.config import VOTE_ONLY_DATASETS
from pipeline.routes import (
    PRESETS,
    all_combos,
    combos_for,
    compose,
    parse,
    presented,
    resolve,
)


class ComposeTest(unittest.TestCase):
    def test_lighton_third_slot_is_a_tiebreak_route(self) -> None:
        r = compose("dots", "gemini", "lighton")
        self.assertEqual(r.name, "dots+gemini+lighton")
        self.assertEqual(r.main, ("dots", "block"))
        self.assertEqual(r.supplementals, (("gemini", "page_xml"),))
        self.assertEqual(r.tiebreak, "lighton")

    def test_third_model_is_a_direct_vote_route(self) -> None:
        r = compose("dots", "mistral", "surya_block")
        self.assertEqual(r.name, "dots+mistral+surya_block")
        self.assertEqual(
            r.supplementals, (("mistral", "block"), ("surya", "block"))
        )
        self.assertIsNone(r.tiebreak)

    def test_order_never_matters_same_set_same_route(self) -> None:
        # dots+gemini+mistral IS dots+mistral+gemini — combinations are
        # SETS, only unique combinations exist
        canonical = compose("dots", "gemini", "mistral")
        self.assertEqual(canonical.name, "dots+gemini+mistral")
        self.assertEqual(canonical, compose("dots", "mistral", "gemini"))
        self.assertEqual(canonical, compose("mistral", "dots", "gemini"))
        self.assertEqual(canonical, compose("gemini", "mistral", "dots"))
        # dots anywhere in the set is ALWAYS the main
        self.assertEqual(canonical.main, ("dots", "block"))
        self.assertEqual(
            compose("surya_block", "gemini", "dots").name,
            "dots+gemini+surya_block",
        )

    def test_dots_is_always_main(self) -> None:
        r = compose("gemini", "dots", "lighton")
        self.assertEqual(r.main, ("dots", "block"))
        self.assertEqual(r.name, "dots+gemini+lighton")
        self.assertEqual(r, compose("dots", "gemini", "lighton"))

    def test_bbox_model_is_main_when_no_dots(self) -> None:
        r = compose("gemini", "mistral", "surya_block")
        self.assertEqual(r.main, ("mistral", "block"))
        self.assertEqual(r.name, "mistral+gemini+surya_block")

    def test_priority_decides_the_main(self) -> None:
        # no slot-order tiebreak: the fixed MAIN_PRIORITY ranking picks
        # the main (dots > mistral > surya_block), so the same set
        # always composes the same route
        r = compose("surya_block", "gemini", "mistral")
        self.assertEqual(r.main, ("mistral", "block"))
        self.assertEqual(r.name, "mistral+gemini+surya_block")
        self.assertEqual(r, compose("mistral", "surya_block", "gemini"))

    def test_lighton_tiebreak_requires_dots(self) -> None:
        # the cached tiebreak crops are cut from dots blocks — a
        # non-dots tiebreak combination could never vote
        with self.assertRaises(ValueError):
            compose("mistral", "gemini", "lighton")
        with self.assertRaises(ValueError):
            compose("surya_block", "mistral", "lighton")
        with self.assertRaises(ValueError):
            parse("surya_block+gemini+lighton")

    def test_surya_line_is_retired(self) -> None:
        # surya's line mode is not a model
        with self.assertRaises(ValueError):
            compose("surya_line", "gemini", "lighton")
        with self.assertRaises(ValueError):
            parse("dots+surya_line+lighton")

    def test_lighton_rejected_in_primary_slots(self) -> None:
        with self.assertRaises(ValueError):
            compose("lighton", "dots", "gemini")
        with self.assertRaises(ValueError):
            compose("dots", "lighton", "gemini")

    def test_duplicates_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compose("dots", "dots", "lighton")
        with self.assertRaises(ValueError):
            compose("dots", "mistral", "mistral")

    def test_unknown_model_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compose("dots", "tesseract", "lighton")

    def test_bbox_requirement_message(self) -> None:
        # unreachable through the UI (gemini is the only bbox-less
        # model), but hand-built names must still fail clearly
        with self.assertRaises(ValueError):
            compose("gemini", "gemini", "lighton")


class ParseResolveTest(unittest.TestCase):
    def test_parse_roundtrips_canonical_names(self) -> None:
        for combo in ("dots+gemini+lighton", "mistral+gemini+surya_block"):
            self.assertEqual(parse(combo).name, combo)

    def test_parse_rejects_malformed(self) -> None:
        with self.assertRaises(ValueError):
            parse("dots+gemini")
        with self.assertRaises(ValueError):
            parse("mistral")

    def test_resolve_accepts_presets_and_combos(self) -> None:
        self.assertEqual(resolve("mistral").name, "dots+mistral+lighton")
        self.assertEqual(resolve("three_way").name, "dots+mistral+surya_block")
        # a non-canonical ordering is an ALIAS of the canonical route
        self.assertEqual(
            resolve("mistral+gemini+dots").name, "dots+gemini+mistral"
        )
        self.assertEqual(
            resolve("surya_block+gemini+dots").name,
            "dots+gemini+surya_block",
        )

    def test_presets_all_compose(self) -> None:
        for name, combo in PRESETS.items():
            r = compose(*combo)
            self.assertEqual(r.main[0], "dots", name)


class AllCombosTest(unittest.TestCase):
    def test_every_unique_combination_once(self) -> None:
        combos = all_combos()
        names = [r.name for r in combos]
        self.assertEqual(len(names), len(set(names)))
        # 3 dots+lighton tiebreak pairs + C(4,3)=4 vote trios
        self.assertEqual(len(names), 7)
        # every preset is among them
        for combo in PRESETS.values():
            self.assertIn(compose(*combo).name, names)
        # ordering twins never appear
        self.assertIn("dots+mistral+surya_block", names)
        self.assertNotIn("dots+surya_block+mistral", names)
        self.assertIn("dots+gemini+surya_block", names)
        self.assertNotIn("surya_block+gemini+dots", names)
        # lighton tiebreaks exist only WITH dots (cache coverage)
        self.assertIn("dots+gemini+lighton", names)
        self.assertNotIn("mistral+gemini+lighton", names)
        self.assertNotIn("mistral+surya_block+lighton", names)
        self.assertNotIn("surya_block+gemini+lighton", names)


class PresentedCombosTest(unittest.TestCase):
    """What a dataset PRESENTS: everything, or — where the crop cache is
    too incomplete to compare fairly — its vote trios alone."""

    VOTE_ONLY = "volumes"

    def test_a_vote_only_dataset_presents_no_tiebreaks(self) -> None:
        self.assertIn(self.VOTE_ONLY, VOTE_ONLY_DATASETS)
        combos = combos_for(self.VOTE_ONLY)
        self.assertEqual(len(combos), 4)
        self.assertTrue(all(r.tiebreak is None for r in combos))
        self.assertFalse(presented(self.VOTE_ONLY, "dots+mistral+lighton"))
        self.assertTrue(presented(self.VOTE_ONLY, "dots+mistral+surya_block"))

    def test_withholding_is_opt_in(self) -> None:
        """A dataset nobody has ruled on presents all 7 — a new set is
        never quietly reported as vote-only."""
        combos = combos_for("a-set-nobody-configured")
        self.assertEqual(len(combos), 7)
        self.assertTrue(
            presented("a-set-nobody-configured", "dots+mistral+lighton")
        )

    def test_preset_aliases_are_checked_by_canonical_name(self) -> None:
        # 'three_way' is a vote alias, 'mistral' a tiebreak one
        self.assertTrue(presented(self.VOTE_ONLY, "three_way"))
        self.assertFalse(presented(self.VOTE_ONLY, "mistral"))
        # a non-canonical ordering resolves before the check
        self.assertTrue(presented(self.VOTE_ONLY, "surya_block+mistral+dots"))

    def test_an_uncomposable_name_is_not_presented(self) -> None:
        self.assertFalse(presented("volumes", "dots+dots+dots"))
        self.assertFalse(presented("1k", "nonsense"))
