"""Assemble tests: verdict substitution (including insertions at their
anchor), low-confidence marks in the final HTML, the styling union,
footnote-mark and star-page rendering, image placeholders, and
escaping. Compare records come from the real compare stage so the two
stages cannot drift apart."""

from __future__ import annotations

from unittest import TestCase

from pipeline.core import assemble, compare, normalize
from pipeline.tests.test_compare import (
    BBOX,
    CropCache,
    blocks_for,
    norm_rec,
    recon_rec,
    toks,
)


def compared(
    norm: dict,
    recon: dict,
    blocks: list[dict],
    tiebreak: str | None = None,
    reads: dict[int, str] | None = None,
) -> dict:
    cache = CropCache(reads) if reads is not None else None
    return compare.compare_streams(norm, recon, blocks, tiebreak, cache)


class ResolveTokensTest(TestCase):
    """The final stream: main tokens with the verdicts applied."""

    def test_agreeing_streams_pass_main_through(self) -> None:
        norm = norm_rec(
            toks("one two three"),
            ("mistral", "block", toks("one two three")),
            ("surya", "block", toks("one two three")),
        )
        recon = recon_rec([(0, "content", "one two three")])
        cmp = compared(norm, recon, blocks_for([0]))
        final = assemble.resolve_tokens(norm, cmp, recon)
        self.assertEqual([t["key"] for t in final], ["one", "two", "three"])
        self.assertFalse(any(t["substituted"] for t in final))

    def test_majority_substitutes_the_winning_reading(self) -> None:
        # both supplementals read "brawn": main is outvoted
        norm = norm_rec(
            toks("the quick brown fox"),
            ("mistral", "block", toks("the quick brawn fox")),
            ("surya", "block", toks("the quick brawn fox")),
        )
        recon = recon_rec([(0, "content", "the quick brown fox")])
        cmp = compared(norm, recon, blocks_for([0]))
        final = assemble.resolve_tokens(norm, cmp, recon)
        self.assertEqual(
            [t["key"] for t in final], ["the", "quick", "brawn", "fox"]
        )
        sub = final[2]
        self.assertTrue(sub["substituted"])
        self.assertEqual(sub["item"], 0)  # re-anchored to the main block
        self.assertFalse(sub["low_confidence"])

    def test_insertion_lands_between_its_anchors(self) -> None:
        # both supplementals carry a word the main dropped
        norm = norm_rec(
            toks("one two four"),
            ("mistral", "block", toks("one two three four")),
            ("surya", "block", toks("one two three four")),
        )
        recon = recon_rec([(0, "content", "one two four")])
        cmp = compared(norm, recon, blocks_for([0]))
        final = assemble.resolve_tokens(norm, cmp, recon)
        self.assertEqual(
            [t["key"] for t in final], ["one", "two", "three", "four"]
        )
        self.assertTrue(final[2]["substituted"])

    def test_low_confidence_keeps_main_and_flags_it(self) -> None:
        # three-way split: every stream reads differently
        norm = norm_rec(
            toks("the quick brown fox"),
            ("mistral", "block", toks("the quick brawn fox")),
            ("surya", "block", toks("the quick crown fox")),
        )
        recon = recon_rec([(0, "content", "the quick brown fox")])
        cmp = compared(norm, recon, blocks_for([0]))
        final = assemble.resolve_tokens(norm, cmp, recon)
        self.assertEqual(
            [t["key"] for t in final], ["the", "quick", "brown", "fox"]
        )
        self.assertTrue(final[2]["low_confidence"])
        self.assertFalse(final[2]["substituted"])

    def test_tiebreak_substitution(self) -> None:
        # the cached LightOn read sides with the supplemental
        main = "the quick brown fox jumps over"
        supp = "the quick brawn fox jumps over"
        norm = norm_rec(toks(main), ("mistral", "block", toks(supp)))
        recon = recon_rec([(0, "content", main)])
        cmp = compared(
            norm,
            recon,
            blocks_for([0]),
            tiebreak="lighton",
            reads={0: supp},
        )
        final = assemble.resolve_tokens(norm, cmp, recon)
        self.assertEqual(final[2]["key"], "brawn")
        self.assertTrue(final[2]["substituted"])


class StylingUnionTest(TestCase):
    """Aligned-by-key styling union (display only)."""

    def test_supplemental_emphasis_joins_agreeing_units(self) -> None:
        supp = toks("one two three")
        supp[1]["styling"] = ["em"]
        norm = norm_rec(toks("one two three"), ("mistral", "block", supp))
        recon = recon_rec([(0, "content", "one two three")])
        cmp = compared(
            norm, recon, blocks_for([0]), tiebreak="lighton", reads={}
        )
        final = assemble.final_stream(norm, cmp, recon)
        self.assertEqual(final[1]["styling"], ["em"])
        self.assertEqual(final[0]["styling"], [])

    def test_disagreeing_units_take_no_union(self) -> None:
        supp = toks("one dos three")
        supp[1]["styling"] = ["strong"]
        norm = norm_rec(toks("one two three"), ("mistral", "block", supp))
        recon = recon_rec([(0, "content", "one two three")])
        cmp = compared(
            norm, recon, blocks_for([0]), tiebreak="lighton", reads={}
        )
        final = assemble.final_stream(norm, cmp, recon)
        self.assertEqual(final[1]["styling"], [])


class StarPageTest(TestCase):
    """Gemini parallel-reporter star pages re-inserted by alignment."""

    def test_star_inserted_at_the_aligned_position(self) -> None:
        gem = toks("one two three")
        gem[1]["stars"] = ["306"]
        norm = norm_rec(
            toks("one two three"),
            ("gemini", "page_xml", gem),
            ("surya", "block", toks("one two three")),
        )
        cmp = compared(
            norm,
            recon_rec([(0, "content", "one two three")]),
            blocks_for([0]),
        )
        rec = assemble.assemble_page(
            norm,
            cmp,
            recon_rec([(0, "content", "one two three")]),
            blocks_for([0]),
        )
        self.assertEqual(rec["metrics"]["star_pages"], 1)
        self.assertIn('<span class="star-page">★306</span>', rec["html"])
        # the marker follows its aligned word
        self.assertLess(rec["html"].index("two"), rec["html"].index("★306"))

    def test_star_on_a_winning_token_is_never_doubled(self) -> None:
        # gemini wins the dispute; its token carries the marker in
        gem = toks("one dos three")
        gem[1]["stars"] = ["306"]
        norm = norm_rec(
            toks("one two three"),
            ("gemini", "page_xml", gem),
            ("surya", "block", toks("one dos three")),
        )
        cmp = compared(
            norm,
            recon_rec([(0, "content", "one two three")]),
            blocks_for([0]),
        )
        rec = assemble.assemble_page(
            norm,
            cmp,
            recon_rec([(0, "content", "one two three")]),
            blocks_for([0]),
        )
        self.assertEqual(rec["html"].count("★306"), 1)
        self.assertEqual(rec["metrics"]["star_pages"], 0)  # rode the win


class RenderTest(TestCase):
    """The final HTML: skeleton, marks, placeholders, escaping."""

    def _page(
        self,
        main_text: str = "one two three",
        supp_text: str | None = None,
        items: list[tuple[int, str, str]] | None = None,
        main_toks: list[dict] | None = None,
        block_ids: list[int] | None = None,
    ) -> dict:
        supp_text = supp_text or main_text
        recon = recon_rec(items or [(0, "content", main_text)])
        norm = norm_rec(
            main_toks or toks(main_text),
            ("mistral", "block", toks(supp_text)),
        )
        blocks = blocks_for(block_ids or [0])
        cmp = compared(norm, recon, blocks, tiebreak="lighton", reads={})
        return assemble.assemble_page(norm, cmp, recon, blocks)

    def test_roles_pick_their_elements(self) -> None:
        items = [
            (0, "heading", "one two three"),
            (1, "content", "four"),
            (2, "blockquote", "five"),
        ]
        stream = toks("one two three") + toks("four", item=1)
        stream += toks("five", item=2)
        rec = self._page(
            "one two three four five",
            items=items,
            main_toks=stream,
        )
        self.assertIn('<h2 data-item="0" data-role="heading">', rec["html"])
        self.assertIn('<p data-item="1" data-role="content">', rec["html"])
        self.assertIn(
            '<blockquote data-item="2" data-role="blockquote">', rec["html"]
        )
        self.assertEqual(rec["text"], "one two three\nfour\nfive")

    def test_low_confidence_span_is_marked(self) -> None:
        # no cached read: the dispute keeps main, low-confidence
        rec = self._page(
            "the quick brown fox jumps over", "the quick brawn fox jumps over"
        )
        self.assertIn(
            '<mark class="low-confidence" data-dispute="0">brown</mark>',
            rec["html"],
        )
        self.assertEqual(rec["low_confidence"], [0])

    def test_empty_low_confidence_dispute_gets_a_marker(self) -> None:
        # the supplemental read an extra word the main never saw and no
        # cached read resolves it: the main side is EMPTY, so a
        # zero-width ∅ marker keeps the dispute visible
        rec = self._page(
            "alpha bravo charlie delta", "alpha bravo xx charlie delta"
        )
        self.assertIn(
            '<mark class="low-confidence" data-dispute="0" '
            'title="mistral read “xx”">'
            '<span class="lc-marker">∅</span></mark>',
            rec["html"],
        )
        # the marker carries no content: not in the text, not a unit
        self.assertEqual(rec["text"], "alpha bravo charlie delta")
        self.assertEqual(rec["metrics"]["units"], 4)
        self.assertEqual(rec["metrics"]["n_low_confidence"], 1)

    def test_high_risk_adds_its_class(self) -> None:
        rec = self._page(
            "alpha bravo charlie delta echo golf",
            "alpha bravo charlie whiskey echo golf",
        )
        self.assertIn('class="low-confidence high-risk"', rec["html"])
        self.assertEqual(rec["metrics"]["n_high_risk"], 1)

    def test_marks_and_styling_render_on_the_word(self) -> None:
        stream = toks("one two three")
        stream[1]["marks"] = ["4"]
        stream[1]["styling"] = ["em", "strong"]
        rec = self._page(main_toks=stream)
        self.assertIn("<em><strong>two</strong></em><sup>4</sup>", rec["html"])
        self.assertNotIn("<sup>", rec["text"])

    def test_image_items_become_figure_placeholders(self) -> None:
        items = [(0, "content", "one two three"), (1, "image", "")]
        rec = self._page(items=items, block_ids=[0, 1])
        self.assertIn(
            f'<figure data-item="1" data-bbox='
            f'"{",".join(str(c) for c in BBOX[1])}"></figure>',
            rec["html"],
        )

    def test_figure_url_renders_the_crop_inline(self) -> None:
        # a consumer that serves page crops gets the image rendered
        items = [(0, "content", "one two three"), (1, "image", "")]
        recon = recon_rec(items)
        norm = norm_rec(
            toks("one two three"),
            ("mistral", "block", toks("one two three")),
        )
        blocks = blocks_for([0, 1])
        cmp = compared(norm, recon, blocks, tiebreak="lighton", reads={})
        final = assemble.final_stream(norm, cmp, recon)
        html = assemble.render_html(
            final,
            recon["main"],
            blocks,
            figure_url=lambda b: f"/pagecrop/{'_'.join(map(str, b))}.png",
        )
        self.assertIn(
            '<figure data-item="1" data-bbox="10,70,200,120">'
            '<img src="/pagecrop/10_70_200_120.png"',
            html,
        )

    def test_displays_are_escaped(self) -> None:
        stream = toks("one x three")
        stream[1]["display"] = "<b>&fish"
        rec = self._page(main_toks=stream)
        self.assertIn("&lt;b&gt;&amp;fish", rec["html"])
        self.assertNotIn("<b>", rec["html"])

    def test_reorder_keeps_main_placement_unmarked(self) -> None:
        # the anchor chunk must out-length the moved chunk (the diff
        # anchors on the longest common block), and the moved chunk
        # must reach the reorder floor of five units
        moved = "one two three four five"
        anchor = "alpha bravo charlie delta echo foxtrot"
        main = f"{anchor} {moved}"
        supp = f"{moved} {anchor}"
        rec = self._page(main, supp)
        self.assertNotIn("<mark", rec["html"])
        self.assertTrue(rec["text"].startswith("alpha"))
        self.assertEqual(rec["metrics"]["n_low_confidence"], 0)

    def test_metrics_count_substitutions(self) -> None:
        main = "the quick brown fox jumps over"
        supp = "the quick brawn fox jumps over"
        norm = norm_rec(toks(main), ("mistral", "block", toks(supp)))
        recon = recon_rec([(0, "content", main)])
        cmp = compared(
            norm,
            recon,
            blocks_for([0]),
            tiebreak="lighton",
            reads={0: supp},
        )
        rec = assemble.assemble_page(norm, cmp, recon, blocks_for([0]))
        m = rec["metrics"]
        self.assertEqual(m["substitutions"], 1)
        self.assertEqual(m["substituted_units"], 1)
        self.assertEqual(m["units"], 6)
        self.assertIn("brawn", rec["text"])
        self.assertNotIn("brown", rec["text"])


class GlueTest(TestCase):
    """Presentation restores the source spacing: units the unit split
    separated re-attach when their provenance is contiguous."""

    def test_split_punctuation_reattaches(self) -> None:
        # REAL normalized tokens: `held,` and `(per curiam)` split into
        # word/symbol units; `¶ 12` was genuinely spaced in the source
        source = "The court held, (per curiam) ¶ 12"
        items = [{"id": 0, "role": "content", "html": source}]
        tokens = normalize.normalize_items(items, "dots")["tokens"]
        norm = {
            "main": {"engine": "dots", "tokens": tokens},
            "supplementals": [
                {"engine": "mistral", "unit": "block", "tokens": tokens}
            ],
        }
        recon = recon_rec([(0, "content", source)])
        cmp = compared(
            norm, recon, blocks_for([0]), tiebreak="lighton", reads={}
        )
        rec = assemble.assemble_page(norm, cmp, recon, blocks_for([0]))
        self.assertEqual(rec["text"], source)
        self.assertIn("held, (per curiam) ¶ 12", rec["html"])

    def test_glue_survives_a_substitution_boundary(self) -> None:
        # the word before a glued comma loses its dispute: the comma
        # re-attaches to the SUBSTITUTED reading
        main = toks("the fine brown fox")
        main[2]["span"] = [9, 14]  # "brown"
        comma = {"display": ",", "key": ",", "item": 0, "span": [14, 15]}
        main = main[:3] + [comma] + main[3:]
        supp = toks("the fine brawn fox")
        supp[2]["span"] = [9, 14]
        supp = supp[:3] + [dict(comma)] + supp[3:]
        norm = norm_rec(main, ("mistral", "block", supp))
        recon = recon_rec([(0, "content", "the fine brown, fox")])
        cmp = compared(
            norm,
            recon,
            blocks_for([0]),
            tiebreak="lighton",
            reads={0: "the fine brawn , fox"},
        )
        rec = assemble.assemble_page(norm, cmp, recon, blocks_for([0]))
        self.assertIn("brawn,", rec["text"])


class DiffFlagTest(TestCase):
    """Tokens flagged `diff` / `style_diff` (the route-compare view)
    render wrapped."""

    def _final(self) -> tuple[list[dict], dict]:
        norm = norm_rec(
            toks("one two three"), ("mistral", "block", toks("one two three"))
        )
        recon = recon_rec([(0, "content", "one two three")])
        cmp = compared(
            norm, recon, blocks_for([0]), tiebreak="lighton", reads={}
        )
        return assemble.final_stream(norm, cmp, recon), recon

    def test_diff_run_is_wrapped(self) -> None:
        final, recon = self._final()
        final[1]["diff"] = True
        html = assemble.render_html(final, recon["main"], blocks_for([0]))
        self.assertIn('<span class="route-diff">two</span>', html)

    def test_style_diff_run_is_wrapped(self) -> None:
        final, recon = self._final()
        final[1]["style_diff"] = True
        html = assemble.render_html(final, recon["main"], blocks_for([0]))
        self.assertIn('<span class="style-diff">two</span>', html)
