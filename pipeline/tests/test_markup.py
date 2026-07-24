import unittest

from pipeline.core.markup import html_to_html, md_to_html
from pipeline.engines.gemini import decode


class MdToHtmlTest(unittest.TestCase):
    def test_italic_and_bold(self) -> None:
        self.assertEqual(
            md_to_html("see *Searle* and **must**"),
            "see <em>Searle</em> and <strong>must</strong>",
        )

    def test_mistral_superscript_forms(self) -> None:
        self.assertEqual(md_to_html("word.$^{16}$"), "word.<sup>16</sup>")
        self.assertEqual(md_to_html("word.^{4}"), "word.<sup>4</sup>")

    def test_text_is_escaped(self) -> None:
        self.assertEqual(
            md_to_html("a < b & *c*"), "a &lt; b &amp; <em>c</em>"
        )

    def test_bullet_and_plain_asterisks_survive(self) -> None:
        self.assertEqual(md_to_html("* * *"), "* * *")


class HtmlToHtmlTest(unittest.TestCase):
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

    def test_unclosed_tag_closed(self) -> None:
        self.assertEqual(html_to_html("<i>oops"), "<em>oops</em>")

    def test_data_escaped(self) -> None:
        self.assertEqual(html_to_html("<p>a < b</p>"), "a &lt; b")


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
