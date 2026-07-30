"""The final read of a region: majority agreement, then a word vote."""

from django.test import SimpleTestCase

from pipeline.core import consensus


def reads(**by_engine: str) -> dict[str, dict]:
    return {e: {"text": t, "html": t} for e, t in by_engine.items()}


class WholeRegionTests(SimpleTestCase):
    def test_all_engines_agreeing_is_unanimous(self) -> None:
        r = consensus.resolve(reads(dots="a b", mistral="a b", surya="a b"))
        self.assertEqual(r["agreement"], consensus.UNANIMOUS)
        self.assertEqual(r["text"], "a b")
        self.assertEqual(r["n_low_confidence"], 0)

    def test_two_of_three_is_a_majority(self) -> None:
        r = consensus.resolve(reads(dots="a b", mistral="a b", surya="a X"))
        self.assertEqual(r["agreement"], consensus.MAJORITY)
        self.assertEqual(r["agreeing"], ["dots", "mistral"])
        self.assertEqual(r["text"], "a b")

    def test_a_whole_region_winner_keeps_its_styling(self) -> None:
        """The point of resolving whole: the winner's HTML carries
        through. Word voting cannot do that."""
        engines = {
            "dots": {"text": "Ex parte", "html": "<em>Ex parte</em>"},
            "mistral": {"text": "Ex parte", "html": "<em>Ex parte</em>"},
            "surya": {"text": "Ex Parte", "html": "Ex Parte"},
        }
        self.assertEqual(
            consensus.resolve(engines)["html"], "<em>Ex parte</em>"
        )

    def test_the_minority_engine_wins_nothing(self) -> None:
        r = consensus.resolve(reads(dots="X", mistral="a b", surya="a b"))
        self.assertEqual(r["text"], "a b")
        self.assertNotIn("dots", r["agreeing"])

    def test_one_engine_alone_is_reported_as_such(self) -> None:
        r = consensus.resolve(reads(surya="only me"))
        self.assertEqual(r["agreement"], consensus.SINGLE)
        self.assertEqual(r["source"], "surya")

    def test_two_engines_must_both_agree(self) -> None:
        """With two readings a 1-1 split is no majority, so it votes."""
        r = consensus.resolve(reads(dots="a b", mistral="a X"))
        self.assertEqual(r["agreement"], consensus.VOTED)


class WordVoteTests(SimpleTestCase):
    def test_engines_erring_in_different_places_recover_the_text(
        self,
    ) -> None:
        """The reason word voting exists: no two engines agree on the
        whole region, but every single word has a majority."""
        r = consensus.resolve(
            reads(
                dots="the quick brown fox",
                mistral="the quick brown FOX",
                surya="the quick BROWN fox",
            )
        )
        self.assertEqual(r["agreement"], consensus.VOTED)
        self.assertEqual(r["text"], "the quick brown fox")
        self.assertEqual(r["n_low_confidence"], 0)

    def test_a_word_no_two_engines_agree_on_is_marked(self) -> None:
        r = consensus.resolve(
            reads(dots="the A end", mistral="the B end", surya="the C end")
        )
        self.assertEqual(r["n_low_confidence"], 1)
        self.assertIn('<mark class="low-confidence">A</mark>', r["html"])
        self.assertEqual(r["text"], "the A end")

    def test_the_priority_engine_breaks_the_tie(self) -> None:
        r = consensus.resolve(
            reads(mistral="the B end", surya="the C end", dots="the A end")
        )
        self.assertEqual(r["source"], "dots")
        self.assertEqual(r["text"], "the A end")

    def test_a_word_the_others_agree_on_is_inserted(self) -> None:
        r = consensus.resolve(
            reads(
                dots="the end X",
                mistral="the very end Y",
                surya="the very end Z",
            )
        )
        self.assertIn("very", r["text"])

    def test_a_word_the_others_omit_is_dropped(self) -> None:
        r = consensus.resolve(
            reads(
                dots="the very end X",
                mistral="the end Y",
                surya="the end Z",
            )
        )
        self.assertNotIn("very", r["text"])

    def test_a_word_split_resolves_to_the_majority_spelling(self) -> None:
        """Engines disagree on tokenization, not content."""
        r = consensus.resolve(
            reads(
                dots="co operate now A",
                mistral="cooperate now B",
                surya="cooperate now C",
            )
        )
        self.assertIn("cooperate", r["text"])
        self.assertNotIn("co operate", r["text"])

    def test_two_engines_disagreeing_mark_every_difference(self) -> None:
        """With one other engine no word can reach a majority, so the
        base's reading survives — marked, never silently chosen."""
        r = consensus.resolve(
            reads(dots="the quick fox", mistral="the quiet fox")
        )
        self.assertEqual(r["agreement"], consensus.VOTED)
        self.assertEqual(r["n_low_confidence"], 1)
        self.assertIn('<mark class="low-confidence">quick</mark>', r["html"])
        self.assertEqual(r["text"], "the quick fox")

    def test_a_word_only_one_of_two_engines_has_is_not_inserted(
        self,
    ) -> None:
        """An insertion needs a majority too; with two engines it can
        never have one. The extra word stays visible in that engine's
        own read on the region card."""
        r = consensus.resolve(reads(dots="the end", mistral="the very end"))
        self.assertNotIn("very", r["text"])
        self.assertNotIn("very", r["html"])

    def test_marked_text_is_the_plain_reading(self) -> None:
        """`text` is what the region says; the marks live in `html`."""
        r = consensus.resolve(
            reads(dots="a A z", mistral="a B z", surya="a C z")
        )
        self.assertNotIn("<mark", r["text"])
        self.assertIn("<mark", r["html"])

    def test_engine_text_is_escaped_into_the_vote_output(self) -> None:
        r = consensus.resolve(
            reads(dots="a <b> z", mistral="a <i> z", surya="a <u> z")
        )
        self.assertIn("&lt;b&gt;", r["html"])
        self.assertNotIn("<b>", r["html"])
