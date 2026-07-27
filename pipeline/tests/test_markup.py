import unittest

from pipeline.core.markup import (
    dots_md_to_html,
    gemini_repair,
    html_to_html,
    lighton_markup,
    mistral_md_to_html,
)
from pipeline.engines.gemini import decode, is_refusal


class DotsMarkdownTest(unittest.TestCase):
    def test_italic_and_bold(self) -> None:
        self.assertEqual(
            dots_md_to_html("see *Searle* and **must**"),
            "see <em>Searle</em> and <strong>must</strong>",
        )

    def test_bold_italic_nests_properly(self) -> None:
        self.assertEqual(
            dots_md_to_html("***bold italic***"),
            "<strong><em>bold italic</em></strong>",
        )

    def test_superscript_forms(self) -> None:
        self.assertEqual(dots_md_to_html("word.$^{16}$"), "word.<sup>16</sup>")
        self.assertEqual(dots_md_to_html("word.$^{[4]}$"), "word.<sup>4</sup>")

    def test_separator_line_protected(self) -> None:
        self.assertEqual(dots_md_to_html("* * *"), "* * *")

    def test_leading_bullet_becomes_content_glyph(self) -> None:
        self.assertEqual(dots_md_to_html("* first item"), "● first item")
        self.assertEqual(dots_md_to_html("• dotted"), "● dotted")

    def test_heading_markers_dropped(self) -> None:
        self.assertEqual(dots_md_to_html("## I. BACKGROUND"), "I. BACKGROUND")

    def test_text_is_escaped(self) -> None:
        self.assertEqual(
            dots_md_to_html("a < b & *c*"), "a &lt; b &amp; <em>c</em>"
        )


class MistralMarkdownTest(unittest.TestCase):
    def test_list_markers_fold_to_bullet(self) -> None:
        self.assertEqual(mistral_md_to_html("- item one"), "● item one")
        self.assertEqual(mistral_md_to_html("* item two"), "● item two")

    def test_image_refs_dropped(self) -> None:
        self.assertEqual(mistral_md_to_html("![img](x.png)"), "")

    def test_math_dollar_stripped_currency_kept(self) -> None:
        self.assertEqual(mistral_md_to_html("$Id.$ at 376"), "Id. at 376")
        self.assertEqual(
            mistral_md_to_html("a $100,000 fine"), "a $100,000 fine"
        )

    def test_caret_superscript(self) -> None:
        self.assertEqual(
            mistral_md_to_html("space.^{7}"), "space.<sup>7</sup>"
        )

    def test_underscore_emphasis(self) -> None:
        self.assertEqual(mistral_md_to_html("_agree_"), "<em>agree</em>")

    def test_heading_markers_dropped(self) -> None:
        self.assertEqual(mistral_md_to_html("# HEADING"), "HEADING")


class LightonMarkupTest(unittest.TestCase):
    def test_latex_wraps_removed_entirely(self) -> None:
        self.assertEqual(lighton_markup("\\mathcal{M} word"), " word")

    def test_math_delimiters_stripped(self) -> None:
        self.assertEqual(lighton_markup("$Id.$"), "Id.")

    def test_caret_and_emphasis(self) -> None:
        self.assertEqual(lighton_markup("^4 ruling"), "<sup>4</sup> ruling")
        self.assertEqual(
            lighton_markup("_italic_ **bold**"),
            "<em>italic</em> <strong>bold</strong>",
        )

    def test_residual_underscores_dropped(self) -> None:
        self.assertEqual(lighton_markup("stray_underscore"), "strayunderscore")


class SuryaHtmlTest(unittest.TestCase):
    def test_i_b_map_to_em_strong(self) -> None:
        self.assertEqual(
            html_to_html("<p><i>Id.</i> at <b>376</b></p>"),
            "<em>Id.</em> at <strong>376</strong>",
        )

    def test_sup_kept_unknown_unwrapped(self) -> None:
        self.assertEqual(
            html_to_html("<div><span>word</span><sup>7</sup></div>"),
            "word<sup>7</sup>",
        )

    def test_list_items_on_own_lines_with_bullet(self) -> None:
        self.assertEqual(
            html_to_html("<ul><li>first<li>second</ul>"),
            "● first\n● second",
        )

    def test_literal_bullet_inside_li_not_doubled(self) -> None:
        # surya sometimes writes BOTH the <li> tag and a literal bullet
        self.assertEqual(
            html_to_html("<ul><li>• First item<li>Second</ul>"),
            "● First item\n● Second",
        )
        self.assertEqual(
            html_to_html("<li><i>• styled</i> item"),
            "● <em>styled</em> item",
        )

    def test_block_boundaries_become_newlines(self) -> None:
        self.assertEqual(html_to_html("<p>one</p><p>two</p>"), "one\ntwo")
        self.assertEqual(
            html_to_html("line one<br>line two"), "line one\nline two"
        )
        # a single wrapping block adds no stray breaks
        self.assertEqual(html_to_html("<p>only</p>"), "only")

    def test_unclosed_tag_closed(self) -> None:
        self.assertEqual(html_to_html("<i>oops"), "<em>oops</em>")

    def test_data_escaped(self) -> None:
        self.assertEqual(html_to_html("<p>a < b</p>"), "a &lt; b")


class GeminiRepairTest(unittest.TestCase):
    def test_code_fence_stripped(self) -> None:
        self.assertEqual(
            gemini_repair('```xml\n<p x="1">A</p>\n```'),
            '<p x="1">A</p>',
        )

    def test_bare_ampersand_escaped_entities_kept(self) -> None:
        self.assertEqual(gemini_repair("<p>A & B</p>"), "<p>A &amp; B</p>")
        self.assertEqual(
            gemini_repair("<p>A &amp; B &#38; C</p>"),
            "<p>A &amp; B &#38; C</p>",
        )


class GeminiDecodeTest(unittest.TestCase):
    def test_unknown_leaf_tags_preserved(self) -> None:
        xml = (
            "<page><opinion><author>Justice X delivered.</author>"
            "<p>Body text.</p></opinion></page>"
        )
        blocks = decode(xml)["blocks"]
        self.assertEqual(
            [(b["tag"], b["text"]) for b in blocks],
            [("author", "Justice X delivered."), ("p", "Body text.")],
        )

    def test_inline_detail_tags_styled(self) -> None:
        xml = (
            "<page><p>See <citation><em>Searle</em>, 2010 ME 89</citation>"
            "<footnotemark>4</footnotemark></p></page>"
        )
        b = decode(xml)["blocks"][0]
        self.assertEqual(b["text"], "See Searle, 2010 ME 894")
        self.assertEqual(
            b["styled"], "See <em>Searle</em>, 2010 ME 89<sup>4</sup>"
        )

    def test_wrappers_recursed_not_emitted(self) -> None:
        xml = (
            "<page><footnotes><footnote>n1</footnote>"
            "<footnote>n2</footnote></footnotes></page>"
        )
        blocks = decode(xml)["blocks"]
        self.assertEqual([b["tag"] for b in blocks], ["footnote"] * 2)

    def test_parse_error_reported(self) -> None:
        out = decode("<page><p>broken")
        self.assertTrue(out["parse_error"])
        self.assertEqual(out["blocks"], [])

    def test_fenced_payload_parses(self) -> None:
        out = decode("```xml\n<page><p>body</p></page>\n```")
        self.assertIsNone(out["parse_error"])
        self.assertEqual(out["blocks"][0]["text"], "body")

    def test_bare_ampersand_repaired(self) -> None:
        out = decode("<page><p>Johnson & Sons</p></page>")
        self.assertIsNone(out["parse_error"])
        self.assertEqual(out["blocks"][0]["text"], "Johnson & Sons")

    def test_footnote_inherits_coords_from_nested_p(self) -> None:
        # <footnote> carries no coordinates; its nested <p> does — the
        # block must inherit them (a3d.340.1__p1 case)
        xml = (
            "<page><footnotes>"
            '<footnote label="4" id="fn4">'
            '<p col="L" x="181" y="764">4. Note text.</p>'
            "</footnote></footnotes></page>"
        )
        b = decode(xml)["blocks"][0]
        self.assertEqual(b["tag"], "footnote")
        self.assertEqual(b["attrs"]["x"], "181")
        self.assertEqual(b["attrs"]["y"], "764")
        self.assertEqual(b["attrs"]["col"], "L")
        self.assertEqual(b["attrs"]["label"], "4")

    def test_img_tag_is_one_block_not_a_wrapper(self) -> None:
        # gemini nests <p> inside <img>; the caption must NOT leak out as
        # ordinary paragraphs (the sct.145.2291__p2 case)
        xml = (
            '<page><img type="scan" col="L" x="285" y="85">'
            "<p>The band started to play.</p></img>"
            "<p>Real body text.</p></page>"
        )
        blocks = decode(xml)["blocks"]
        self.assertEqual([b["tag"] for b in blocks], ["img", "p"])
        self.assertEqual(blocks[0]["text"], "The band started to play.")

    def test_tail_text_after_block_preserved(self) -> None:
        xml = "<page><p>body</p>STRAY TAIL<p>more</p></page>"
        blocks = decode(xml)["blocks"]
        self.assertEqual(
            [(b["tag"], b["text"]) for b in blocks],
            [("p", "body"), ("page", "STRAY TAIL"), ("p", "more")],
        )

    def test_wrapper_own_text_preserved(self) -> None:
        xml = "<page><opinion>PER CURIAM.<p>body</p></opinion></page>"
        blocks = decode(xml)["blocks"]
        self.assertEqual(
            [(b["tag"], b["text"]) for b in blocks],
            [("opinion", "PER CURIAM."), ("p", "body")],
        )

    def test_root_leading_text_preserved(self) -> None:
        xml = "<page>LEAD-IN<p>body</p></page>"
        blocks = decode(xml)["blocks"]
        self.assertEqual(
            [(b["tag"], b["text"]) for b in blocks],
            [("page", "LEAD-IN"), ("p", "body")],
        )

    def test_whitespace_between_tags_is_not_content(self) -> None:
        xml = "<page>\n  <p>a</p>\n  <p>b</p>\n</page>"
        blocks = decode(xml)["blocks"]
        self.assertEqual([b["text"] for b in blocks], ["a", "b"])

    def test_inline_star_page_placed_at_position(self) -> None:
        xml = (
            "<page><p>before "
            '<parallelpagenumber pagenumber="306">*306</parallelpagenumber>'
            "after</p></page>"
        )
        b = decode(xml)["blocks"][0]
        self.assertEqual(b["star_pages"], ["306"])
        # the marker sits IN the stream at its position (placement by
        # alignment happens at normalize/compare via the token anchor)
        self.assertEqual(b["text"], "before ★306 after")
        self.assertIn("★306", b["styled"])

    def test_block_star_page_is_a_placed_marker(self) -> None:
        xml = (
            "<page><parallelpagenumber>★307</parallelpagenumber>"
            "<p>body</p></page>"
        )
        blocks = decode(xml)["blocks"]
        self.assertEqual(blocks[0]["tag"], "parallelpagenumber")
        self.assertEqual(blocks[0]["star_pages"], ["307"])
        self.assertEqual(blocks[0]["text"], "★307")
        self.assertEqual(blocks[0]["styled"], "★307")


class IsRefusalTest(unittest.TestCase):
    def test_missing_or_empty_is_refusal(self) -> None:
        self.assertTrue(is_refusal(None))
        self.assertTrue(is_refusal(""))
        self.assertTrue(is_refusal("   \n"))

    def test_marker_payload_is_refusal(self) -> None:
        self.assertTrue(is_refusal("RECITATION"))
        self.assertTrue(is_refusal("blocked: PROHIBITED_CONTENT"))

    def test_xml_is_not_refusal(self) -> None:
        self.assertFalse(is_refusal("<page><p>text</p></page>"))
        # marker WORDS inside real XML content are not a refusal
        self.assertFalse(is_refusal("<page><p>a RECITATION of</p></page>"))


class CaretGuardTest(unittest.TestCase):
    def test_long_digit_runs_never_half_convert(self) -> None:
        # ^{2020} / ^456 are not footnote marks: no <sup>20</sup>20
        self.assertEqual(
            mistral_md_to_html("rose ^{2020} high"), "rose ^2020 high"
        )
        self.assertEqual(lighton_markup("^456 stays"), "^456 stays")

    def test_short_caret_marks_still_convert(self) -> None:
        self.assertEqual(lighton_markup("^4 ok"), "<sup>4</sup> ok")
        self.assertEqual(
            mistral_md_to_html("space.^{7}"), "space.<sup>7</sup>"
        )
