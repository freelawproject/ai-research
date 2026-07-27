import unittest

from pipeline.routes import PRESETS, compose, parse, resolve


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

    def test_dots_in_first_two_is_always_main(self) -> None:
        r = compose("gemini", "dots", "lighton")
        self.assertEqual(r.main, ("dots", "block"))
        self.assertEqual(r.name, "dots+gemini+lighton")
        # slot order between the primaries doesn't matter for the name
        self.assertEqual(r, compose("dots", "gemini", "lighton"))

    def test_bbox_model_is_main_when_no_dots(self) -> None:
        r = compose("gemini", "mistral", "lighton")
        self.assertEqual(r.main, ("mistral", "block"))
        self.assertEqual(r.name, "mistral+gemini+lighton")

    def test_two_bbox_models_slot_order_decides(self) -> None:
        r = compose("surya_block", "mistral", "lighton")
        self.assertEqual(r.main, ("surya", "block"))
        self.assertEqual(r.name, "surya_block+mistral+lighton")

    def test_dots_in_third_slot_is_a_vote_member_not_main(self) -> None:
        r = compose("gemini", "mistral", "dots")
        self.assertEqual(r.main, ("mistral", "block"))
        self.assertEqual(
            r.supplementals, (("gemini", "page_xml"), ("dots", "block"))
        )
        self.assertIsNone(r.tiebreak)

    def test_surya_line_can_be_main(self) -> None:
        r = compose("surya_line", "gemini", "lighton")
        self.assertEqual(r.main, ("surya", "line"))

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
        for combo in ("dots+gemini+lighton", "mistral+gemini+dots"):
            self.assertEqual(parse(combo).name, combo)

    def test_parse_rejects_malformed(self) -> None:
        with self.assertRaises(ValueError):
            parse("dots+gemini")
        with self.assertRaises(ValueError):
            parse("mistral")

    def test_resolve_accepts_presets_and_combos(self) -> None:
        self.assertEqual(resolve("mistral").name, "dots+mistral+lighton")
        self.assertEqual(resolve("three_way").name, "dots+mistral+surya_block")
        self.assertEqual(
            resolve("mistral+gemini+dots").name, "mistral+gemini+dots"
        )

    def test_presets_all_compose(self) -> None:
        for name, combo in PRESETS.items():
            r = compose(*combo)
            self.assertEqual(r.main[0], "dots", name)
