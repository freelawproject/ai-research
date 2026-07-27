import unittest
from dataclasses import replace
from typing import cast

from pipeline.core.normalize import (
    REGISTRY,
    Rule,
    Token,
    apply_rules,
    normalize_items,
    registry_hash,
    registry_summary,
    stream_rules,
    tokenize,
)


def _items_from(spec: object) -> list[dict]:
    """Build reconstruct items from an example input: a string (one
    item), a list of strings, or a list of (text, role) pairs."""
    entries = [spec] if isinstance(spec, str) else cast(list, spec)
    items = []
    for i, entry in enumerate(entries):
        text, role = entry if isinstance(entry, tuple) else (entry, "content")
        items.append({"id": i, "role": role, "html": text})
    return items


def _items(*texts: str) -> list[dict]:
    return _items_from(list(texts))


_FIELDS = {
    "key": ["key"],
    "display": ["display"],
    "both": ["key", "display"],
    "stream": ["key"],
}


class RegistryExamplesTest(unittest.TestCase):
    """Every rule's example strings ARE its unit tests. Stream rules:
    tokenize the input, apply that rule alone, and the changed field(s)
    joined with spaces must equal the expected stream. Decode rules:
    fn(input) must equal the expected styled string."""

    def test_stream_examples(self) -> None:
        for rule in REGISTRY:
            if rule.kind != "stream" or rule.fn is None:
                continue
            for source, expected in rule.examples:
                out = cast(list[Token], rule.fn(tokenize(_items_from(source))))
                for field in _FIELDS[rule.applies_to]:
                    with self.subTest(rule=rule.name, example=source):
                        self.assertEqual(
                            " ".join(getattr(t, field) for t in out),
                            expected,
                        )

    def test_decode_examples(self) -> None:
        for rule in REGISTRY:
            if rule.kind != "decode" or rule.fn is None:
                continue
            for source, expected in rule.examples:
                with self.subTest(rule=rule.name, example=source):
                    self.assertEqual(rule.fn(cast(str, source)), expected)

    def test_every_executable_rule_has_examples(self) -> None:
        for rule in REGISTRY:
            if rule.fn is not None:
                self.assertTrue(rule.examples, f"{rule.name} has no examples")


class TokenizeTest(unittest.TestCase):
    def test_spans_map_back_to_item_html(self) -> None:
        items = _items("The court  held:", "Reversed.")
        tokens = tokenize(items)
        self.assertEqual(
            [t.display for t in tokens],
            ["The", "court", "held:", "Reversed."],
        )
        for t in tokens:
            src = items[t.item]["html"]
            self.assertEqual(src[t.span[0] : t.span[1]], t.display)

    def test_display_equals_key_and_case_preserved(self) -> None:
        # casing is deliberately NOT normalized (keys stay verbatim)
        for t in tokenize(_items("The COURT Held")):
            self.assertEqual(t.display, t.key)

    def test_styling_carried_on_tokens(self) -> None:
        tokens = tokenize(_items("see <em>Searle, 2010</em> now"))
        self.assertEqual(
            [t.styling for t in tokens], [(), ("em",), ("em",), ()]
        )

    def test_sup_becomes_mark_styled_token(self) -> None:
        tokens = tokenize(_items("word<sup>4</sup>"))
        self.assertEqual(
            [(t.display, t.styling) for t in tokens],
            [("word", ()), ("4", ("sup",))],
        )

    def test_entities_unescaped_in_display(self) -> None:
        tokens = tokenize(_items("A&amp;P stores"))
        self.assertEqual(tokens[0].display, "A&P")

    def test_image_items_and_empty_text_skipped(self) -> None:
        items = [
            {"id": 0, "role": "image", "html": "IN-IMAGE JUNK"},
            {"id": 1, "role": "content", "html": ""},
            {"id": 2, "role": "content", "html": "kept"},
        ]
        self.assertEqual([t.display for t in tokenize(items)], ["kept"])


class ChainBehaviorTest(unittest.TestCase):
    """Rule interplay over the FULL chain (apply_rules with the real
    registry)."""

    def _keys(self, *texts: str, engine: str = "dots") -> list[str]:
        tokens, _ = apply_rules(tokenize(_items(*texts)), engine)
        return [t.key for t in tokens]

    def test_marks_ride_position_faithfully(self) -> None:
        # marks stay where they appeared: after the last unit of the
        # split word (so rendering keeps deadline."¹⁸ order)
        tokens, _ = apply_rules(
            tokenize(_items('word<sup>4</sup> deadline."18 UCL.³')), "dots"
        )
        self.assertEqual(
            [(t.display, t.marks) for t in tokens],
            [
                ("word", ("4",)),
                ("deadline", ()),
                (".", ()),
                ('"', ("18",)),
                ("UCL", ()),
                (".", ("3",)),
            ],
        )

    def test_wrap_join_provenance_covers_both_fragments(self) -> None:
        tokens, _ = apply_rules(
            tokenize(_items("ends with charac-", "terized next")), "dots"
        )
        joined = tokens[2]
        self.assertEqual(joined.display, "characterized")
        self.assertEqual(joined.key, "characterized")
        self.assertEqual(joined.item, 0)
        self.assertEqual(joined.wrap, (1, 0, 7))

    def test_wrap_join_keeps_semantic_dash_then_splits(self) -> None:
        tokens, _ = apply_rules(tokenize(_items("2509– 2511.")), "dots")
        self.assertEqual(
            [t.display for t in tokens], ["2509", "–", "2511", "."]
        )
        # the dash unit's glyph folds; its presence is compared
        self.assertEqual([t.key for t in tokens], ["2509", "-", "2511", "."])
        # each unit carries the EXACT span of the fragment it came from
        # ("2509–" = chars 0-5, "2511." = chars 6-11 of the same item);
        # no unit straddles the join here, so none needs the wrap record
        self.assertEqual(
            [t.span for t in tokens], [(0, 4), (4, 5), (6, 10), (10, 11)]
        )
        self.assertTrue(all(t.wrap is None for t in tokens))
        self.assertTrue(all(t.item == 0 for t in tokens))

    def test_separator_block_survives_asterisk_rules(self) -> None:
        tokens, _ = apply_rules(tokenize(_items("* * *")), "dots")
        self.assertEqual(
            [(t.display, t.key) for t in tokens], [("* * *", "***")]
        )

    def test_star_asterisks_inside_prose_are_not_a_separator(self) -> None:
        self.assertEqual(self._keys("word * more"), ["word", "more"])

    def test_dash_is_a_compared_unit(self) -> None:
        # an em dash is a punctuation UNIT: present in the stream, glyph
        # folded — its absence on the other side is a punct-level diff
        self.assertEqual(self._keys("— word"), ["-", "word"])

    def test_punctuation_compared_separately_from_words(self) -> None:
        # `word,` vs `word.` must agree on the word unit
        a = self._keys("word,")
        b = self._keys("word.")
        self.assertEqual(a[0], b[0])
        self.assertEqual((a[1], b[1]), (",", "."))

    def test_glued_quotes_no_longer_matter(self) -> None:
        # engines attach quotes to arbitrary neighbors; unit streams agree
        self.assertEqual(
            self._keys("conduct\" 'lacks"),
            self._keys("conduct \"'lacks"),
        )

    def test_smart_quotes_fold_in_units(self) -> None:
        self.assertEqual(
            self._keys("“conduct” don’t"),
            ['"', "conduct", '"', "don", "'", "t"],
        )

    def test_mark_stays_when_word_splits(self) -> None:
        tokens, _ = apply_rules(tokenize(_items("word<sup>7</sup> .")), "dots")
        self.assertEqual(
            [(t.display, t.marks) for t in tokens],
            [("word", ("7",)), (".", ())],
        )

    def test_star_page_anchors_to_preceding_token(self) -> None:
        tokens, _ = apply_rules(
            tokenize(_items("body ★306 more", "★307", "after")), "gemini"
        )
        self.assertEqual([t.key for t in tokens], ["body", "more", "after"])
        self.assertEqual(tokens[0].stars, ("306",))
        # a block-level marker anchors across items to the previous word
        self.assertEqual(tokens[1].stars, ("307",))

    def test_star_marker_untouched_on_other_engines(self) -> None:
        # the rule is gemini-layer; other engines never emit the marker
        tokens, applied = apply_rules(tokenize(_items("★306 x")), "dots")
        rec = next(r for r in applied if r["rule"] == "gemini-star-pages")
        self.assertFalse(rec["applied"])

    def test_records_follow_stream_rule_order(self) -> None:
        for engine in ("dots", "mistral", "gemini", "surya"):
            _, applied = apply_rules(tokenize(_items("X")), engine)
            self.assertEqual(
                [r["rule"] for r in applied],
                [r.name for r in stream_rules()],
            )


class ApplyRulesMechanicsTest(unittest.TestCase):
    """Registry mechanics, exercised with a synthetic rule."""

    @staticmethod
    def _fold_rule(layer: str = "shared") -> Rule:
        return Rule(
            name="test-fold",
            layer=layer,
            applies_to="key",
            description="test-only: key = casefold(display)",
            examples=(("The COURT Held", "the court held"),),
            fn=lambda tokens: [
                replace(t, key=t.key.casefold()) for t in tokens
            ],
        )

    def test_change_records_cover_changed_runs_only(self) -> None:
        _, applied = apply_rules(
            tokenize(_items("all lower The End")),
            "dots",
            registry=(self._fold_rule(),),
        )
        rec = applied[0]
        self.assertEqual(rec["rule"], "test-fold")
        self.assertTrue(rec["applied"])
        self.assertEqual(rec["n_changed"], 2)
        (change,) = rec["changes"]
        self.assertEqual(change["at"], 2)
        self.assertEqual([t["key"] for t in change["before"]], ["The", "End"])
        self.assertEqual([t["key"] for t in change["after"]], ["the", "end"])

    def test_untouched_stream_records_no_changes(self) -> None:
        _, applied = apply_rules(
            tokenize(_items("already folded")),
            "dots",
            registry=(self._fold_rule(),),
        )
        self.assertEqual(applied[0]["n_changed"], 0)
        self.assertEqual(applied[0]["changes"], [])

    def test_engine_layer_rule_skipped_for_other_engines(self) -> None:
        registry = (self._fold_rule(layer="dots"),)
        tokens, applied = apply_rules(
            tokenize(_items("The COURT")), "surya", registry=registry
        )
        self.assertFalse(applied[0]["applied"])
        self.assertEqual([t.key for t in tokens], ["The", "COURT"])
        tokens, applied = apply_rules(
            tokenize(_items("The COURT")), "dots", registry=registry
        )
        self.assertTrue(applied[0]["applied"])
        self.assertEqual([t.key for t in tokens], ["the", "court"])


class NormalizeItemsTest(unittest.TestCase):
    def test_artifact_record_shape(self) -> None:
        rec = normalize_items(_items("The <em>Court</em>"), "dots")
        self.assertEqual([t["key"] for t in rec["tokens"]], ["The", "Court"])
        self.assertEqual(
            [t["display"] for t in rec["tokens"]], ["The", "Court"]
        )
        token = rec["tokens"][0]
        self.assertEqual(token["item"], 0)
        self.assertEqual(token["span"], [0, 3])
        self.assertNotIn("styling", token)  # omitted while empty
        self.assertEqual(rec["tokens"][1]["styling"], ["em"])
        self.assertEqual(len(rec["rules"]), len(stream_rules()))

    def test_token_dict_keeps_marks_and_wrap_when_set(self) -> None:
        t = Token(
            display="word",
            key="word",
            item=0,
            span=(0, 4),
            marks=("4",),
            wrap=(1, 0, 3),
        )
        d = t.as_dict()
        self.assertEqual(d["marks"], ["4"])
        self.assertEqual(d["wrap"], [1, 0, 3])
        self.assertNotIn("styling", d)


class RegistryVersionTest(unittest.TestCase):
    def test_hash_is_short_stable_hex(self) -> None:
        h = registry_hash()
        self.assertEqual(h, registry_hash())
        self.assertEqual(len(h), 12)
        int(h, 16)  # hex or raise

    def test_summary_matches_registry(self) -> None:
        summary = registry_summary()
        self.assertEqual(
            [s["name"] for s in summary], [r.name for r in REGISTRY]
        )
        for s in summary:
            self.assertTrue(s["description"])
            self.assertIn(s["kind"], ("stream", "decode"))
