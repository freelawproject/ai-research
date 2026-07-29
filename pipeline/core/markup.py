"""Native engine markup -> presentation HTML (the engine-DECODE layer).

The end product is HTML, so every engine's format is converted to one
sanitized tag convention (<em>, <strong>, <sup>, <sub>, <u>, and the
bullet glyph ●) at decode time. Text is escaped first; the only tags in
the output are the ones this module emits. The shared normalization
layer (core/normalize.py) then walks this convention identically for
every engine — the registry there documents each decode rule with
examples that are executed as unit tests.

Per-engine entry points: dots_md_to_html (dots' markdown-ish output),
mistral_md_to_html (Mistral markdown, incl. LightOn-style math/emphasis
via lighton_markup), lighton_markup (LightOn crop reads, used at the
tiebreak stage), html_to_html (surya HTML), gemini_repair (pre-parse
cleanup of gemini XML payloads).
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# ── markdown emphasis (escape first; subs then emit tags) ────────────────
# Bold-italic before bold before italic so each longer delimiter isn't
# eaten by a shorter one. Emphasis content may not start/end with
# whitespace: keeps `* * *` separators and stray asterisks out of <em>.
_BOLD_EM = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", re.S)
_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.S)
_EM = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
# LightOn/Mistral underscore emphasis: _word_ / __word__
_U_STRONG = re.compile(r"(?<![\w\\])__(?!_)(.+?)__(?![\w])", re.S)
_U_EM = re.compile(r"(?<![\w\\])_(?!_)([^_\n]+?)_(?![\w])")
# Superscript forms: $^{16}$ / $^{[4]}$ (dots), ^{4} / ^4 (mistral,
# lighton caret). A braced form must CLOSE its brace and a bare form
# must consume every digit — ^{2020} / ^456 are not footnote marks and
# stay untouched rather than being half-converted.
_SUP_MATH = re.compile(r"\$\^\{?\[?([^${}\[\]]{1,8})\]?\}?\$")
# Inline subscript math: dots writes chemical/technical subscripts as
# LaTeX ($NO_x$ = NO with a subscript x) — the notation, not content.
_SUB_MATH = re.compile(r"\$(\w+)_\{?(\w{1,6})\}?\$")
_SUP_CARET = re.compile(r"\^(?:\{\[?(\d{1,2})\]?\}|\[?(\d{1,2})\]?(?!\d))")


def _sup_caret_tag(m: re.Match[str]) -> str:
    return f"<sup>{m.group(1) or m.group(2)}</sup>"


# ── line-level markdown structure ────────────────────────────────────────
# A separator line (* * *) is protected before bullet/emphasis handling —
# it is a section break, neither a list marker nor italics (note #12).
_SEP_LINE = re.compile(r"^[ \t]*(?:\*[ \t]*){2,}$", re.M)
_BULLET_DOTS = re.compile(r"^[ \t]*[*+•·][ \t]+", re.M)
_BULLET_MISTRAL = re.compile(r"^[ \t]*[-*•][ \t]+", re.M)
_MD_HEAD = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.M)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
# Mistral sometimes leaks its own block coordinates into the block TEXT
# as [BBOX]x0,y0,x1,y1[/BBOX] (normalized 0-1 floats, occasionally with
# the closing tag missing). Coordinates are not reading content: they
# arrive already as the block's own bbox field, and left in place they
# tokenize into dozens of digit/comma units that no other engine has.
# The inner run is restricted to COORDINATE characters (digits, dots,
# commas, spaces/tabs — never letters or a newline): with the closing tag
# missing, a permissive inner match would swallow the sentence after it.
_MISTRAL_BBOX = re.compile(
    r"\[\s*BBOX\s*\][\d.,\t ]*(?:\[\s*/\s*BBOX\s*\])?", re.I
)


def strip_mistral_bbox(text: str) -> str:
    """Drop leaked [BBOX]…[/BBOX] coordinate markers. Applied to the
    block's plain text AND its markup, so the marker reaches neither
    the reconstruction (where block text sizes the tiebreak gate) nor
    the token stream."""
    return _MISTRAL_BBOX.sub("", text or "").lstrip()


# ── LightOn LaTeX / math cleanup ─────────────────────────────────────────
# LaTeX wraps are removed ENTIRELY, contents and all — they are spurious
# decoder output, not reporter text (`\mathcal{M}` -> ``). A `$` right
# before a digit is CURRENCY ($100,000) and is preserved; only
# math-delimiter `$` (as in `$Id.$`) is stripped.
_LATEX_WRAP = re.compile(r"\\[a-zA-Z]+\s*\{([^{}]*)\}")
_LATEX_CMD = re.compile(r"\\[a-zA-Z]+|\\[,;:!> ]")
_MATH_DOLLAR = re.compile(r"\$(?!\s*\d)")


def _protect_separators(text: str) -> tuple[str, list[str]]:
    seps: list[str] = []

    def stash(m: re.Match[str]) -> str:
        seps.append(m.group(0))
        return f"\x00SEP{len(seps) - 1}\x00"

    return _SEP_LINE.sub(stash, text), seps


def _restore_separators(text: str, seps: list[str]) -> str:
    for j, s in enumerate(seps):
        text = text.replace(f"\x00SEP{j}\x00", s)
    return text


def _md_emphasis(escaped: str) -> str:
    out = _BOLD_EM.sub(r"<strong><em>\1</em></strong>", escaped)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    return _EM.sub(r"<em>\1</em>", out)


def dots_md_to_html(text: str) -> str:
    """dots' markdown -> tag convention. A table block (dots emits
    literal <table>/<tr>/<td> HTML) goes through the HTML sanitizer —
    structure kept, attributes stripped. Otherwise: `* * *` separator
    lines are protected first; leading `* ` list markers become the
    content bullet ● (never confused with *italics*); leading `#`
    heading markers are structural markup and are dropped; `$^{[N]}$`
    superscripts become <sup> marks; **/* emphasis becomes
    <strong>/<em>."""
    if "<table" in (text or ""):
        return html_to_html(text)
    t, seps = _protect_separators(text or "")
    t = _BULLET_DOTS.sub("● ", t)
    t = _MD_HEAD.sub("", t)
    t = _restore_separators(t, seps)
    out = html.escape(t, quote=False)
    out = _SUP_MATH.sub(r"<sup>\1</sup>", out)
    out = _SUB_MATH.sub(r"\1<sub>\2</sub>", out)
    return _md_emphasis(out)


def _delatex(t: str) -> str:
    for _ in range(3):  # peel nested wraps
        t2 = _LATEX_WRAP.sub("", t)
        if t2 == t:
            break
        t = t2
    t = _LATEX_CMD.sub("", t)
    return t.replace("{", "").replace("}", "")


def _math_emphasis_to_html(text: str) -> str:
    """The math/emphasis cleanup Mistral and LightOn both need — the
    two models share the same output habits ($Id.$ math delimiters,
    ^{7} caret superscripts, _italic_ underscore emphasis), so the
    implementation is shared; each engine's registry rule stands on
    its own."""
    t = _delatex(_MATH_DOLLAR.sub("", text or ""))
    out = html.escape(t, quote=False)
    out = _SUP_CARET.sub(_sup_caret_tag, out)
    out = _U_STRONG.sub(r"<strong>\1</strong>", out)
    out = _md_emphasis(out)
    out = _U_EM.sub(r"<em>\1</em>", out)
    return out.replace("_", "")


def lighton_markup(text: str) -> str:
    """LightOn crop read -> tag convention: LaTeX artifacts and `$…$`
    math delimiters removed ($ before a digit = currency, preserved),
    caret superscripts (^4 / ^{4}) become <sup> marks, **/__ bold and
    */_ italics become <strong>/<em>, leftover emphasis-residue
    underscores dropped."""
    return _math_emphasis_to_html(text)


def mistral_md_to_html(text: str) -> str:
    """Mistral markdown -> tag convention: a table block (Mistral emits
    literal <table>/<tr>/<th>/<td> HTML) goes through the HTML
    sanitizer — structure kept, attributes stripped. Otherwise:
    `[BBOX]…[/BBOX]` coordinate leakage dropped, `![img](url)` refs
    dropped
    (not text), line-leading list markers (- * •) become ●, `#` heading
    markers dropped, `$…$` math delimiters stripped ($ before a digit =
    currency, preserved), LaTeX artifacts removed, caret superscripts
    (^{7}) become <sup> marks, **/__/*/_ emphasis becomes
    <strong>/<em>."""
    if "<table" in (text or ""):
        return html_to_html(text)
    t = _MD_IMAGE.sub("", strip_mistral_bbox(text))
    t, seps = _protect_separators(t)
    t = _BULLET_MISTRAL.sub("● ", t)
    t = _MD_HEAD.sub("", t)
    t = _restore_separators(t, seps)
    return _math_emphasis_to_html(t)


# ── gemini XML payload repair (pre-parse) ────────────────────────────────
_FENCE = re.compile(r"^```(?:xml)?[ \t]*\n?|\n?```[ \t]*$", re.M)
_BARE_AMP = re.compile(r"&(?!amp;|lt;|gt;|quot;|apos;|#)")


def gemini_repair(raw: str) -> str:
    """Gemini sometimes wraps the XML in a markdown code fence or emits a
    bare `&`; both break the XML parse and both are repairable without
    touching content."""
    txt = _FENCE.sub("", (raw or "").strip()).strip()
    return _BARE_AMP.sub("&amp;", txt)


class _Sanitizer(HTMLParser):
    """Rebuild an HTML fragment keeping only allowlisted inline tags.
    Block-level tags imply LINE BREAKS (a <li> starts a new line, a
    </p> ends one) — the structure the HTML carries is preserved as
    newlines instead of being flattened away."""

    MAP = {
        "i": "em",
        "em": "em",
        "b": "strong",
        "strong": "strong",
        "sup": "sup",
        "sub": "sub",
        "u": "u",
    }
    # tags whose boundaries imply a line break (surya emits p, div,
    # ul/ol/li, h*, br)
    BREAKS = frozenset(
        {
            "p",
            "div",
            "ul",
            "ol",
            "li",
            "blockquote",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "br",
        }
    )
    # table STRUCTURE is kept (attributes stripped): the end product is
    # HTML, so a parsed table renders as a table. Cells end on their own
    # line — cell texts must never touch (they are separate tokens).
    TABLE_OPEN = {
        "table": "\n<table>\n",
        "thead": "<thead>\n",
        "tbody": "<tbody>\n",
        "tr": "<tr>\n",
        "td": "<td>",
        "th": "<th>",
    }
    TABLE_CLOSE = {
        "table": "</table>\n",
        "thead": "</thead>\n",
        "tbody": "</tbody>\n",
        "tr": "</tr>\n",
        "td": "</td>\n",
        "th": "</th>\n",
    }

    # surya sometimes writes BOTH the <li> tag and a literal bullet
    # glyph inside the item — one bullet must not become two
    LI_GLYPHS = "•◦‣⁃∙·●"

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []
        self.li_lead = False  # next text starts a <li> item

    def handle_starttag(self, tag: str, attrs: object) -> None:
        mapped = self.MAP.get(tag)
        if mapped:
            self.out.append(f"<{mapped}>")
            self.stack.append(mapped)
        elif tag in self.TABLE_OPEN:
            self.out.append(self.TABLE_OPEN[tag])
        elif tag == "li":
            # a list item starts its own line AND keeps its bullet:
            # surya's HTML carries the list structure the plain text
            # drops
            self.out.append("\n● ")
            self.li_lead = True
        elif tag in self.BREAKS:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        mapped = self.MAP.get(tag)
        if mapped and mapped in self.stack:
            while self.stack:  # close down to the matching tag
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == mapped:
                    break
        elif tag in self.TABLE_CLOSE:
            self.out.append(self.TABLE_CLOSE[tag])
        elif tag in self.BREAKS:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self.li_lead:
            lead = data.lstrip()
            if lead:  # first real text of the item
                if lead[0] in self.LI_GLYPHS:
                    lead = lead[1:].lstrip()
                data = lead
                self.li_lead = False
        self.out.append(html.escape(data, quote=False))


_NL_RUN = re.compile(r"[ \t]*\n[ \t\n]*")


def html_to_html(fragment: str) -> str:
    """Engine HTML (surya) -> sanitized HTML. Unknown inline tags are
    unwrapped (their text is kept); i/b map to em/strong; block-tag
    boundaries become newlines (<li> items on their own line with a ●
    bullet)."""
    p = _Sanitizer()
    p.feed(fragment)
    p.close()
    while p.stack:
        p.out.append(f"</{p.stack.pop()}>")
    return _NL_RUN.sub("\n", "".join(p.out)).strip()
