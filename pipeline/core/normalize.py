"""Normalization: canonical tokens + the rule registry.

Reconstruction fixes reading order; normalization turns each
engine's reconstructed stream into canonical tokens for the compare
stage. A token separates what is RENDERED from what is COMPARED:

- ``display`` — the text as the engine wrote it (rendering only). The
  one deliberate exception: a word wrap-split by a plain hyphen is
  joined WITHOUT the hyphen in display too —
  semantic dashes (en dash ranges like 2509–2511) keep their dash.
- ``key`` — the comparison form; the compare stage diffs keys and
  nothing else.
  Casing is deliberately preserved (no folding).
  Words and symbols are separate comparison UNITS (symbol-split):
  `word,` becomes the units `word` + `,`, so a
  punctuation difference never breaks a word match — it shows up as
  exactly a punctuation difference.
- ``item`` / ``span`` — provenance: the reconstruct item the token came
  from and its char span in that item's ``html`` string. A wrap-joined
  token also records the second fragment (``wrap``), so a dispute can
  crop both source bboxes.
- ``styling`` — em/strong/u/sub carried by the token (union on joins).
- ``marks`` — footnote marks riding this word (tag / unicode / bracket
  / glued forms). Marks are never comparison content; their sequences
  get their own comparison flow at compare + resolve.

Tokenization walks the sanitized tag convention every engine's decode
produces (core/markup.py) — one walker, identical for all engines.

Rules live in ONE ordered registry. ``kind="stream"`` rules transform
the token stream here; ``kind="decode"`` entries document the per-engine
format decoding (their fn is the markup function itself). Every rule
declares its layer ("shared" applies to every engine; an engine-named
layer only that engine), what it may change, and example strings — the
examples ARE the unit tests (pipeline/tests/test_normalize.py). The
registry hash is stamped into every artifact. Tokens whose key ends up
empty after the chain carry no content and are dropped.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import cast

from pipeline.core import markup

_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class Token:
    display: str
    key: str
    item: int  # reconstruct item id (dots block / paragraph / gemini block)
    span: tuple[int, int]  # char span in that item's html string
    role: str = "content"  # from the item; not serialized (derivable)
    styling: tuple[str, ...] = ()
    marks: tuple[str, ...] = ()
    # parallel-reporter star pages anchored AFTER this token (gemini):
    # the final output re-inserts ★<page> where this token aligns
    stars: tuple[str, ...] = ()
    # second fragment of a wrap-joined word: (item, start, end)
    wrap: tuple[int, int, int] | None = None
    # display offset where the wrapped fragment begins (transient — set
    # by wrap-join, consumed by symbol-split for exact per-fragment
    # provenance; never serialized)
    wrap_at: int | None = None
    fixed: bool = False  # key is final (separator token); key rules skip

    def as_dict(self) -> dict:
        d: dict = {
            "display": self.display,
            "key": self.key,
            "item": self.item,
            "span": list(self.span),
        }
        if self.styling:
            d["styling"] = list(self.styling)
        if self.marks:
            d["marks"] = list(self.marks)
        if self.stars:
            d["stars"] = list(self.stars)
        if self.wrap:
            d["wrap"] = list(self.wrap)
        return d


@dataclass(frozen=True)
class Rule:
    """One normalization decision.

    ``kind="stream"``: fn transforms the token stream; examples are
    (input, expected) pairs — the input (a string, a list of item
    strings, or a list of (text, role) items) is tokenized, THIS rule
    alone is applied, and the changed field (key and/or display, per
    ``applies_to``) joined with spaces must equal the expected string.

    ``kind="decode"``: fn is the engine's markup function (str -> str);
    examples are (raw, styled) pairs applied directly. fn=None marks a
    structural decode policy documented here but tested in its own
    module's tests.
    """

    name: str
    layer: str  # "shared" | engine name
    applies_to: str  # "key" | "display" | "both" | "styled" | "stream"
    description: str
    examples: tuple[tuple[object, str], ...]
    # stream fn (list[Token] -> list[Token]), decode fn (str -> str),
    # or None for a structural policy implemented elsewhere.
    fn: Callable[..., object] | None
    kind: str = "stream"

    def applies(self, engine: str) -> bool:
        return self.layer in ("shared", engine)


# ── tokenizer: one walk over the sanitized tag convention ────────────────

_TAG_WALK = re.compile(
    r"<(/?)(em|strong|u|sub)\b[^>]*>"  # 1 close?  2 styling tag
    r"|<sup\b[^>]*>(.*?)</sup>"  # 3 mark tag content
    r"|<[^>]+>"  # any other tag (skipped)
    r"|([^<]+)",  # 4 text run
    re.S | re.I,
)
_INNER_TAG = re.compile(r"<[^>]+>")


def tokenize(items: list[dict]) -> list[Token]:
    """The base token stream in reading order: whitespace-split words of
    every non-image item's sanitized html, carrying active styling.
    <sup> content becomes a token styled "sup" — the marks rule folds it
    onto its word. display == key at this point."""
    tokens: list[Token] = []
    for it in items:
        if it.get("role") == "image":
            continue
        src = it.get("html") or it.get("text") or ""
        role = it.get("role", "content")
        active: list[str] = []
        for m in _TAG_WALK.finditer(src):
            if m.group(2) is not None:  # styling tag
                sty = m.group(2).lower()
                if m.group(1):
                    if sty in active:
                        active.remove(sty)
                elif sty not in active:
                    active.append(sty)
            elif m.group(3) is not None:  # <sup> mark
                text = _INNER_TAG.sub("", m.group(3)).strip()
                if text:
                    tokens.append(
                        Token(
                            display=text,
                            key=text,
                            item=it["id"],
                            span=(m.start(), m.end()),
                            role=role,
                            styling=tuple(sorted({*active, "sup"})),
                        )
                    )
            elif m.group(4) is not None:  # text run
                base = m.start(4)
                for w in _WORD.finditer(m.group(4)):
                    tokens.append(
                        Token(
                            display=_unescape(w.group()),
                            key=_unescape(w.group()),
                            item=it["id"],
                            span=(base + w.start(), base + w.end()),
                            role=role,
                            styling=tuple(sorted(active)),
                        )
                    )
    return tokens


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


# ── rule implementations ─────────────────────────────────────────────────


def _key_rule(
    fn: Callable[[str], str],
) -> Callable[[list[Token]], list[Token]]:
    def apply(tokens: list[Token]) -> list[Token]:
        return [t if t.fixed else replace(t, key=fn(t.key)) for t in tokens]

    return apply


def _attach_mark(out: list[Token], pending: list[str], mark: str) -> None:
    """A mark rides the preceding word; marks before any word wait and
    ride the next one."""
    if out:
        out[-1] = replace(out[-1], marks=(*out[-1].marks, mark))
    else:
        pending.append(mark)


def _flush_pending(out: list[Token], pending: list[str]) -> None:
    if pending and out:
        out[0] = replace(out[0], marks=(*pending, *out[0].marks))
        pending.clear()


def _marks_tagged(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    pending: list[str] = []
    for t in tokens:
        if "sup" in t.styling:
            _attach_mark(out, pending, t.display)
        else:
            out.append(t)
            _flush_pending(out, pending)
    return out


_SUP_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_RUN = re.compile(f"[{_SUP_CHARS}]+")
_SUP_TO_DIGIT = str.maketrans(_SUP_CHARS, "0123456789")
_BRACKET_MARK = re.compile(r"(?<![\w])\[(\d{1,2})\](?![\w])")


def _mark_extractor(
    pattern: re.Pattern[str],
    to_mark: Callable[[str], str] = lambda m: m,
) -> Callable[[list[Token]], list[Token]]:
    """A mark shape found inside a token's text: every match becomes a
    mark on the surrounding word (stripped from display and key); a
    token that was ONLY marks attaches them to its neighbors."""

    def apply(tokens: list[Token]) -> list[Token]:
        out: list[Token] = []
        pending: list[str] = []
        for t in tokens:
            found = pattern.findall(t.display)
            if not found:
                out.append(t)
                _flush_pending(out, pending)
                continue
            word = pattern.sub("", t.display).strip()
            if word:
                out.append(
                    replace(t, display=word, key=pattern.sub("", t.key))
                )
                _flush_pending(out, pending)
            for m in found:
                _attach_mark(out, pending, to_mark(m))
        return out

    return apply


_marks_unicode = _mark_extractor(
    _SUP_RUN, lambda run: run.translate(_SUP_TO_DIGIT)
)
_marks_bracketed = _mark_extractor(_BRACKET_MARK)


# A footnote number GLUED onto a word with no space and no markup — the
# form Mistral/LightOn emit (`deadline."18`). Conservative: digits (<=3,
# so `Sept.2020` is safe) right after terminal punctuation (+ optional
# closing quote/bracket), and the word part must contain a letter (so
# `$100,000` / `1,23` are untouched).
_GLUED_MARK = re.compile(r'^(?P<w>.*[.,;:!?]["\'”’)\]}]*)(?P<m>\d{1,3})$')


_STAR_TOKEN = re.compile(r"^★(\d+)$")


def _star_anchor(tokens: list[Token]) -> list[Token]:
    """A ★<page> marker token (gemini's parallel-reporter star page)
    anchors to the PRECEDING token's `stars` and leaves the stream —
    the anchor's alignment against the main engine is what places the
    marker in the final output. A marker before any word rides the
    next token."""
    out: list[Token] = []
    pending: list[str] = []
    for t in tokens:
        m = _STAR_TOKEN.match(t.display)
        if m:
            if out:
                out[-1] = replace(out[-1], stars=(*out[-1].stars, m.group(1)))
            else:
                pending.append(m.group(1))
        else:
            out.append(t)
            if pending:
                out[0] = replace(out[0], stars=(*pending, *out[0].stars))
                pending.clear()
    return out


def _marks_glued(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in tokens:
        gm = _GLUED_MARK.match(t.display)
        if gm and any(c.isalpha() for c in gm.group("w")):
            km = _GLUED_MARK.match(t.key)
            out.append(
                replace(
                    t,
                    display=gm.group("w"),
                    key=km.group("w") if km else gm.group("w"),
                    marks=(*t.marks, gm.group("m")),
                )
            )
        else:
            out.append(t)
    return out


def _separator_blocks(tokens: list[Token]) -> list[Token]:
    """An item whose tokens are ALL asterisks (>=2 total) collapses to
    one fixed separator token."""
    by_item: dict[int, list[Token]] = {}
    for t in tokens:
        by_item.setdefault(t.item, []).append(t)
    seps = {
        item
        for item, run in by_item.items()
        if all(set(t.display) == {"*"} for t in run)
        and sum(len(t.display) for t in run) >= 2
    }
    out: list[Token] = []
    done: set[int] = set()
    for t in tokens:
        if t.item not in seps:
            out.append(t)
        elif t.item not in done:
            done.add(t.item)
            run = by_item[t.item]
            out.append(
                replace(
                    run[0],
                    display="* * *",
                    key="***",
                    span=(run[0].span[0], run[-1].span[1]),
                    fixed=True,
                )
            )
    return out


# Wrap-join triggers on a CONNECTIVE trailing dash — plain/unicode
# hyphens, figure dash, en dash (ranges: 2509–2511), minus. The em dash
# and horizontal bar are parenthetical punctuation between words, never
# connectors. Display drops a plain wrap HYPHEN (charac-terized ->
# characterized) but keeps a semantic dash
# (2509–2511); keys lose the dash either way (dashes-strip).
_WRAP_END = re.compile(r"\w[-‐‑‒–−]$")
_PLAIN_HYPHENS = "-‐‑"
_WORD_START = re.compile(r"\w")


def _wrap_join(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        # keep joining while the (possibly already joined) word still
        # ends in a connective dash — a word split across THREE lines
        # (hy- phen- ated) joins completely
        while (
            i + 1 < len(tokens)
            and not t.fixed
            and _WRAP_END.search(t.display)
            and _WORD_START.match(tokens[i + 1].display)
            and tokens[i + 1].role == t.role  # never cross roles
        ):
            nxt = tokens[i + 1]
            keep = t.display[-1] not in _PLAIN_HYPHENS
            head = t.display if keep else t.display[:-1]
            khead = t.key if keep else t.key[:-1]
            if not keep and len(head) >= 2 and nxt.display.startswith(head):
                # the engine already wrote the COMPLETE word in the
                # continuation block ("Plain-" then "Plaintiff" at a
                # column break) — joining would double the fragment;
                # the continuation carries the whole word
                head, khead = "", ""
            t = replace(
                t,
                display=head + nxt.display,
                key=khead + nxt.key,
                styling=tuple(sorted({*t.styling, *nxt.styling})),
                marks=(*t.marks, *nxt.marks),
                # star anchors from BOTH fragments survive the join
                stars=(*t.stars, *nxt.stars),
                # chained joins record the first + latest fragments
                wrap=(nxt.item, *nxt.span),
                wrap_at=len(head),
            )
            i += 1
        out.append(t)
        i += 1
    return out


# Comparison units: word/digit runs, and every other symbol on its own
# — `word,` vs `word.` agrees on the word and
# differs only on the punctuation unit. Both engines split identically,
# so WHERE an engine glued a quote or paren no longer matters.
_UNIT = re.compile(r"\w+|[^\w\s]")


def _unit_token(t: Token, m: re.Match[str], kp: str, is_last: bool) -> Token:
    """One comparison unit of a split token. Marks and star anchors sit
    where they appeared: after the LAST unit. Spans are display offsets
    mapped back into the item html and clamped to the token's own span;
    for a wrap-joined word, units wholly inside the second fragment get
    that fragment's item + exact span, and the unit that straddles the
    join keeps the `wrap` record (its text spans both bboxes)."""
    a, b = m.start(), m.end()
    marks = t.marks if is_last else ()
    stars = t.stars if is_last else ()
    if t.wrap is not None and t.wrap_at is not None:
        w_item, w_s, w_e = t.wrap
        if a >= t.wrap_at:  # wholly in the second fragment
            return replace(
                t,
                display=m.group(),
                key=kp,
                item=w_item,
                span=(
                    min(w_s + a - t.wrap_at, w_e),
                    min(w_s + b - t.wrap_at, w_e),
                ),
                wrap=None,
                wrap_at=None,
                marks=marks,
                stars=stars,
            )
        if b <= t.wrap_at:  # wholly in the first fragment
            return replace(
                t,
                display=m.group(),
                key=kp,
                span=(
                    min(t.span[0] + a, t.span[1]),
                    min(t.span[0] + b, t.span[1]),
                ),
                wrap=None,
                wrap_at=None,
                marks=marks,
                stars=stars,
            )
        # straddles the join: first-fragment span + the wrap record
        return replace(
            t,
            display=m.group(),
            key=kp,
            span=(min(t.span[0] + a, t.span[1]), t.span[1]),
            wrap_at=None,
            marks=marks,
            stars=stars,
        )
    return replace(
        t,
        display=m.group(),
        key=kp,
        span=(
            min(t.span[0] + a, t.span[1]),
            min(t.span[0] + b, t.span[1]),
        ),
        marks=marks,
        stars=stars,
    )


def _symbol_split(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in tokens:
        parts = list(_UNIT.finditer(t.display))
        kparts = _UNIT.findall(t.key)
        if t.fixed or len(parts) <= 1 or len(parts) != len(kparts):
            # nothing to split, or display/key disagree on structure
            # (post-fold edge case): keep the token whole
            out.append(t)
            continue
        last = len(parts) - 1
        for i, (m, kp) in enumerate(zip(parts, kparts)):
            out.append(_unit_token(t, m, kp, i == last))
    return out


_QUOTES = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "„": '"',
        "″": '"',
        "‘": "'",
        "’": "'",
        "‚": "'",
        "′": "'",
        "`": "'",
    }
)
_BULLETS = str.maketrans({c: "●" for c in "•◦‣⁃∙·"})
_DASHES_FOLD = str.maketrans({c: "-" for c in "‐‑‒–—―−"})
_SQUARES = "■□▪▫◼◻◾◽▬▮▯❑❒█▉▊▋▌▍▎▏▐▀▄▁▂▃▅▆▇░▒▓"
_SQUARES_DEL = str.maketrans({c: "" for c in _SQUARES})


def _squares_strip(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in tokens:
        if t.display and all(c in _SQUARES for c in t.display):
            continue  # a misread redaction bar: dropped, never attached
        out.append(
            t if t.fixed else replace(t, key=t.key.translate(_SQUARES_DEL))
        )
    return out


def _asterisk_residue(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in tokens:
        if t.fixed:
            out.append(t)
        elif set(t.display) == {"*"}:
            continue  # unpaired emphasis marker: dropped
        else:
            # edge asterisks leave display too (the output must match);
            # keys lose every asterisk
            out.append(
                replace(
                    t,
                    display=t.display.strip("*"),
                    key=t.key.replace("*", ""),
                )
            )
    return out


def _heading_markers(tokens: list[Token]) -> list[Token]:
    out: list[Token] = []
    for t in tokens:
        if t.fixed:
            out.append(t)
            continue
        disp = t.display.lstrip("#>")
        if not disp:
            continue
        out.append(
            replace(
                t,
                display=disp,
                key=t.key.lstrip("#>").replace("#", ""),
            )
        )
    return out


# NOTE: the old pipeline's punct-attach / brackets-forward /
# quote-edge-strip rules are deliberately ABSENT: symbol-split makes
# them obsolete. Where an engine glued a quote or paren no
# longer affects the unit stream, and a punctuation difference shows up
# as exactly that.

# ── the registry ─────────────────────────────────────────────────────────

REGISTRY: tuple[Rule, ...] = (
    # marks first: NFKC would fold unicode superscripts (³ -> 3) into
    # keys before the mark rules could extract them
    Rule(
        name="marks-tagged",
        layer="shared",
        applies_to="stream",
        description=(
            "<sup> content is a footnote mark, not body text: it rides "
            "the preceding word's marks (marks before any word ride the "
            "next one) and leaves the key stream. Mark sequences get "
            "their own comparison flow at compare + resolve."
        ),
        examples=(
            ("word<sup>4</sup> next", "word next"),
            ("<sup>4</sup> Footnote text.", "Footnote text."),
        ),
        fn=_marks_tagged,
    ),
    Rule(
        name="marks-unicode",
        layer="shared",
        applies_to="stream",
        description=(
            "dots emits footnote marks as unicode superscript digits "
            "(¹²³), often glued to the word (UCL.³): stripped from the "
            "word and folded to plain digits on its marks."
        ),
        examples=(("UCL.³ holds", "UCL. holds"),),
        fn=_marks_unicode,
    ),
    Rule(
        name="marks-bracketed",
        layer="shared",
        applies_to="stream",
        description=(
            "Bracketed digit refs [4] are footnote marks (dots inlines "
            "them where gemini tags <footnotemark>) — unless glued to a "
            "word character: 57.05[4](a) is a subsection ref, not a "
            "mark."
        ),
        examples=(
            ("died.[4] there", "died. there"),
            ("§ 57.05[4](a)", "§ 57.05[4](a)"),
        ),
        fn=_marks_bracketed,
    ),
    Rule(
        name="marks-glued-digits",
        layer="shared",
        applies_to="stream",
        description=(
            "A footnote number glued onto a word with no markup "
            '(mistral/lighton: deadline."18) splits off as a mark. '
            "Guards: digits <=3 right after terminal punctuation, word "
            "must contain a letter — Sept.2020 and $100,000 untouched."
        ),
        examples=(
            ('deadline."18 passed', 'deadline." passed'),
            ("Sept.2020 filing", "Sept.2020 filing"),
            ("a $100,000 fine", "a $100,000 fine"),
        ),
        fn=_marks_glued,
    ),
    Rule(
        name="gemini-star-pages",
        layer="gemini",
        applies_to="stream",
        description=(
            "A ★<page> marker (parallel-reporter star page, gemini "
            "only — placed into the stream at its position by the "
            "decode) anchors to the preceding token's `stars` and "
            "leaves the comparison stream: the anchor's alignment "
            "against the main engine places the marker in the final "
            "output — placement by text alignment, not just "
            "identification."
        ),
        examples=(("before ★306 after", "before after"),),
        fn=_star_anchor,
    ),
    Rule(
        name="nfkc",
        layer="shared",
        applies_to="key",
        description=(
            "Unicode NFKC fold in keys — ligatures (ﬁ -> fi), width "
            "variants. Casing is deliberately preserved. Runs after the "
            "mark rules so superscript digits are marks, not key text."
        ),
        examples=(("ﬁled brief", "filed brief"),),
        fn=_key_rule(lambda k: unicodedata.normalize("NFKC", k)),
    ),
    Rule(
        name="separator-blocks",
        layer="shared",
        applies_to="stream",
        description=(
            "A block consisting only of asterisks is a section "
            "SEPARATOR: one canonical token `* * *` (key ***), exempt "
            "from bullet and emphasis handling."
        ),
        examples=(("* * *", "***"),),
        fn=_separator_blocks,
    ),
    Rule(
        name="wrap-join",
        layer="shared",
        applies_to="both",
        description=(
            "A word wrap-split by a trailing connective dash joins the "
            "next word (across bboxes or columns) so the wrap is never "
            "a difference; a word split across more than two lines "
            "joins completely. Display drops a plain wrap hyphen "
            "(charac-terized -> characterized) but "
            "keeps a semantic dash (2509–2511); the em dash is "
            "parenthetical and never joins. Joins never cross a "
            "block-role boundary (body 'pre-' must not absorb a "
            "footnote's leading '4.'). When the fragment is a PREFIX "
            "of the next word, the engine already wrote the complete "
            "word in the continuation block ('Plain-' then "
            "'Plaintiff' at a column break) — the join keeps the "
            "whole word once, never 'PlainPlaintiff'. The second "
            "fragment's provenance "
            "is kept so a dispute crops both bboxes, and marks and "
            "star-page anchors from both fragments survive the join."
        ),
        examples=(
            ("charac- terized", "characterized"),
            ("hy- phen- ated", "hyphenated"),
            ("2509– 2511.", "2509–2511."),
            ("word— next", "word— next"),
            ("refer Plain- Plaintiff for", "refer Plaintiff for"),
            (
                [("body pre-", "content"), ("4. footnote", "footnote")],
                "body pre- 4. footnote",
            ),
        ),
        fn=_wrap_join,
    ),
    Rule(
        name="symbol-split",
        layer="shared",
        applies_to="both",
        description=(
            "Words and symbols are SEPARATE comparison units: a token "
            "splits into word/digit runs and "
            "single symbols, so `word,` vs `word.` agrees on the word "
            "and differs only on the punctuation unit. Applies to "
            "punctuation, hyphens, quotes — every non-word symbol. "
            "Where an engine glued a quote or paren no longer matters "
            "(replaces the old punct-attach / brackets-forward / "
            "quote-edge-strip rules). ● § ¶ are content units like any "
            "other symbol. Marks and star-page anchors ride the LAST "
            "unit; units of a wrap-joined word carry the exact span of "
            "the fragment they came from (the unit straddling the join "
            "keeps the two-bbox wrap record)."
        ),
        examples=(
            ("word, next.", "word , next ."),
            ("US-Telecom", "US - Telecom"),
            ("... (per curiam)", ". . . ( per curiam )"),
            ("See § 3553(a)", "See § 3553 ( a )"),
        ),
        fn=_symbol_split,
    ),
    Rule(
        name="quotes-fold",
        layer="shared",
        applies_to="key",
        description=(
            "Smart quotes/apostrophes (and backticks) fold to straight "
            "in keys — engines disagree on glyph, not content."
        ),
        examples=(("“Held” ’tis `so", "\"Held\" 'tis 'so"),),
        fn=_key_rule(lambda k: k.translate(_QUOTES)),
    ),
    Rule(
        name="bullets-fold",
        layer="shared",
        applies_to="key",
        description=(
            "Bullet glyph variants (•◦‣⁃∙·) fold to ● in keys; bullets "
            "are content (every decode emits ● for list markers)."
        ),
        examples=(("• item ◦ two", "● item ● two"),),
        fn=_key_rule(lambda k: k.translate(_BULLETS)),
    ),
    Rule(
        name="dashes-fold",
        layer="shared",
        applies_to="key",
        description=(
            "Dash glyph variants (en/em/figure/bar/minus/unicode "
            "hyphens) fold to a plain hyphen in keys — a dash unit's "
            "PRESENCE is compared (symbol-split), its glyph is not."
        ),
        examples=(("word – word —", "word - word -"),),
        fn=_key_rule(lambda k: k.translate(_DASHES_FOLD)),
    ),
    Rule(
        name="squares-strip",
        layer="shared",
        applies_to="both",
        description=(
            "Filled/hollow squares and Unicode block elements (■□▪█▓…) "
            "are the models' misread redaction bars, not content "
            "(surya reads a redaction as runs of █): removed from "
            "keys, and an all-square token is dropped entirely (never "
            "attached)."
        ),
        examples=(
            ("■ real text ■■", "real text"),
            ("█ █ █ █ kept", "kept"),
        ),
        fn=_squares_strip,
    ),
    Rule(
        name="asterisk-residue",
        layer="shared",
        applies_to="both",
        description=(
            "Asterisks are markdown/emphasis residue, never content: "
            "stripped from keys; a standalone stray * (unpaired italic "
            "marker) is dropped. Separator tokens are exempt."
        ),
        examples=(("*Alabama went *", "Alabama went"),),
        fn=_asterisk_residue,
    ),
    Rule(
        name="heading-markers",
        layer="shared",
        applies_to="both",
        description=(
            "Leading markdown heading markers (#) and dots blockquote "
            "markers (>) are structural markup: stripped from display "
            "and keys (a lone # would otherwise attach backward onto "
            "the previous word — 575#)."
        ),
        examples=(("## HELD: #575", "HELD: 575"),),
        fn=_heading_markers,
    ),
    # ── engine-decode layer (documented + versioned here; the fns run
    # at decode time in core/markup.py / engines/*) ──────────────────────
    Rule(
        name="dots-markdown",
        layer="dots",
        applies_to="styled",
        description=(
            "dots markdown -> tag convention: `* * *` separator lines "
            "protected; leading `* ` list markers -> ●; leading # "
            "heading markers dropped; $^{[N]}$ -> <sup>; inline "
            "subscript math ($NO_x$) -> NO<sub>x</sub>; **/* -> "
            "strong/em. A table block (dots emits literal "
            "<table>/<tr>/<td> HTML) keeps its structure, attributes "
            "stripped — cell text stays comparable unit by unit and "
            "the final renders a real table."
        ),
        examples=(
            ("see *Searle* now", "see <em>Searle</em> now"),
            ("* * *", "* * *"),
            ("* first item", "● first item"),
            ("word.$^{[4]}$", "word.<sup>4</sup>"),
            ("reduce $NO_x$ emissions", "reduce NO<sub>x</sub> emissions"),
            ("## I. BACKGROUND", "I. BACKGROUND"),
            (
                "<table><tr><td>7</td><td>Lakefront</td></tr></table>",
                "<table>\n<tr>\n<td>7</td>\n<td>Lakefront</td>\n</tr>\n"
                "</table>",
            ),
        ),
        fn=markup.dots_md_to_html,
        kind="decode",
    ),
    Rule(
        name="mistral-markdown",
        layer="mistral",
        applies_to="styled",
        description=(
            "Mistral markdown -> tag convention: leaked "
            "[BBOX]x0,y0,x1,y1[/BBOX] coordinate markers dropped (the "
            "engine restating its own bbox, which the block already "
            "carries as a field — not reading content); ![img](url) "
            "refs dropped; -/*/• list markers -> ●; # heading markers "
            "dropped; $…$ math delimiters stripped ($ before a digit "
            "is currency, kept); LaTeX artifacts removed; ^{7} -> "
            "<sup>; **/__/*/_ emphasis -> strong/em. A table block "
            "(literal <table>/<tr>/<th>/<td> HTML) keeps its "
            "structure, attributes stripped."
        ),
        examples=(
            (
                '<table><tr><th colspan="2">Share</th></tr></table>',
                "<table>\n<tr>\n<th>Share</th>\n</tr>\n</table>",
            ),
            (
                "[BBOX]0.1548,0.1455,0.2847,0.1800[/BBOX] ¶60 Under",
                "¶60 Under",
            ),
            # the closing tag is sometimes missing
            ("[BBOX]0.50,0.56,0.58,0.62 the sentence", "the sentence"),
            # a bracketed citation is NOT a coordinate marker
            ("[R.C. 2923.12], illegal", "[R.C. 2923.12], illegal"),
            ("- item one", "● item one"),
            ("$Id.$ at 376", "Id. at 376"),
            ("a $100,000 fine", "a $100,000 fine"),
            ("![img](x.png)", ""),
            ("space.^{7}", "space.<sup>7</sup>"),
            # a longer digit run is NOT a footnote mark: never
            # half-converted (braces drop as LaTeX artifacts)
            ("rose ^{2020} high", "rose ^2020 high"),
            ("_agree_", "<em>agree</em>"),
        ),
        fn=markup.mistral_md_to_html,
        kind="decode",
    ),
    Rule(
        name="lighton-markup",
        layer="lighton",
        applies_to="styled",
        description=(
            "LightOn crop reads -> tag convention: LaTeX artifacts "
            "removed entirely (spurious, not reporter text); $…$ math "
            "delimiters stripped but $ before a digit is CURRENCY and "
            "kept; ^4/^{4} -> <sup>; **/__ -> strong, */_ -> em; "
            "leftover underscores dropped."
        ),
        examples=(
            ("$Id.$", "Id."),
            ("_italic_ **bold**", "<em>italic</em> <strong>bold</strong>"),
            ("^4 ruling", "<sup>4</sup> ruling"),
        ),
        fn=markup.lighton_markup,
        kind="decode",
    ),
    Rule(
        name="surya-html",
        layer="surya",
        applies_to="styled",
        description=(
            "Surya HTML -> tag convention: i/b -> em/strong, sup/sub/u "
            "kept, unknown inline tags unwrapped. Block-tag boundaries "
            "(p, div, ul/ol/li, h*, br) imply LINE BREAKS and are "
            "preserved as newlines; <li> items keep a ● bullet on "
            "their own line (the HTML carries structure the plain "
            "text drops). Table structure (table/thead/tbody/tr/th/td) "
            "is kept, attributes stripped."
        ),
        examples=(
            (
                "<i>Id.</i> at <b>376</b>",
                "<em>Id.</em> at <strong>376</strong>",
            ),
            ("<ul><li>first<li>second</ul>", "● first\n● second"),
            ("<p>one</p><p>two</p>", "one\ntwo"),
            ("line one<br>line two", "line one\nline two"),
            (
                '<table border="1">\n<tr>\n<th>Share</th>\n</tr>\n</table>',
                "<table>\n<tr>\n<th>Share</th>\n</tr>\n</table>",
            ),
        ),
        fn=markup.html_to_html,
        kind="decode",
    ),
    Rule(
        name="gemini-tables",
        layer="gemini",
        applies_to="styled",
        description=(
            "A gemini <table> XML block keeps its rows and cells as "
            "sanitized HTML structure (attributes stripped; cell "
            "content serialized like any inline content) — cell text "
            "stays comparable unit by unit and the final renders a "
            "real table. Structural policy implemented in "
            "engines/gemini.py, tested there."
        ),
        examples=(),
        fn=None,
        kind="decode",
    ),
    Rule(
        name="gemini-repair",
        layer="gemini",
        applies_to="styled",
        description=(
            "Gemini XML payload repair before parsing: markdown code "
            "fences stripped, bare & escaped — both break the parse "
            "without touching content."
        ),
        examples=(
            ('```xml\n<p x="1">A</p>\n```', '<p x="1">A</p>'),
            ("<p>A & B</p>", "<p>A &amp; B</p>"),
        ),
        fn=markup.gemini_repair,
        kind="decode",
    ),
    Rule(
        name="redaction-blocks",
        layer="shared",
        applies_to="stream",
        description=(
            "A block whose crop is >=80% near-black is REDACTION "
            "territory, not content — a redaction-placeholder image, "
            "or text an engine hallucinated over a redacted region "
            "(mistral invents whole passages there): excluded from "
            "the reconstruction and reported (real figures observed "
            "<=0.74, real text far lower; redactions >=0.81). "
            "(Structural — implemented in run.py + reconstruct.)"
        ),
        examples=(),
        fn=None,
        kind="decode",
    ),
)


def registry_hash() -> str:
    """A short stable hash of the registry contents — the normalization
    version stamped into artifacts."""
    payload = json.dumps(
        [
            [
                r.name,
                r.layer,
                r.kind,
                r.applies_to,
                r.description,
                [[str(i), e] for i, e in r.examples],
            ]
            for r in REGISTRY
        ],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def registry_summary() -> list[dict]:
    return [
        {
            "name": r.name,
            "layer": r.layer,
            "kind": r.kind,
            "applies_to": r.applies_to,
            "description": r.description,
            "n_examples": len(r.examples),
        }
        for r in REGISTRY
    ]


def stream_rules() -> tuple[Rule, ...]:
    return tuple(r for r in REGISTRY if r.kind == "stream")


def _sig(t: Token) -> tuple[str, str]:
    return (t.display, t.key)


def _brief(t: Token) -> dict:
    return {"display": t.display, "key": t.key}


def _diff(before: list[Token], after: list[Token]) -> list[dict]:
    """Change records for one rule application: contiguous runs of
    changed tokens, with the before/after streams of each run. Rules may
    merge or split tokens, so both sides are lists."""
    sm = difflib.SequenceMatcher(
        a=[_sig(t) for t in before],
        b=[_sig(t) for t in after],
        autojunk=False,
    )
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        out.append(
            {
                "at": i1,
                "before": [_brief(t) for t in before[i1:i2]],
                "after": [_brief(t) for t in after[j1:j2]],
            }
        )
    return out


def apply_rules(
    tokens: list[Token],
    engine: str,
    registry: tuple[Rule, ...] | None = None,
) -> tuple[list[Token], list[dict]]:
    """Run every applicable stream rule in order, recording what each
    one changed — the viewer's per-rule diff view renders these records
    verbatim. Tokens left with an empty key carry no content and are
    dropped at the end."""
    rules = tuple(
        r
        for r in (registry if registry is not None else REGISTRY)
        if r.kind == "stream"
    )
    applied = []
    for rule in rules:
        if not rule.applies(engine) or rule.fn is None:
            applied.append(
                {
                    "rule": rule.name,
                    "applied": False,
                    "n_changed": 0,
                    "changes": [],
                }
            )
            continue
        after = cast(list[Token], rule.fn(tokens))
        changes = _diff(tokens, after)
        applied.append(
            {
                "rule": rule.name,
                "applied": True,
                "n_changed": sum(
                    max(len(c["before"]), len(c["after"])) for c in changes
                ),
                "changes": changes,
            }
        )
        tokens = after
    return [t for t in tokens if t.key], applied


def normalize_items(items: list[dict], engine: str) -> dict:
    """Normalize one reconstructed stream: base tokens -> registry rules.
    Returns the artifact record for this stream."""
    tokens, applied = apply_rules(tokenize(items), engine)
    return {
        "tokens": [t.as_dict() for t in tokens],
        "rules": applied,
    }
