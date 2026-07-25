"""Native engine markup -> presentation HTML.

The end product is HTML, so every engine's styling markup is converted to a
minimal sanitized vocabulary (<em>, <strong>, <sup>, <sub>, <u>) at decode
time. Text is escaped first; the only tags that can appear in the output
are the ones this module emits.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

# Markdown (dots, mistral). Bold-italic before bold before italic so each
# longer delimiter isn't eaten by a shorter one. Emphasis content may not
# start/end with whitespace: keeps the `* * *` section separator (and
# stray asterisks) out of <em>.
_BOLD_EM = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", re.S)
_BOLD = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", re.S)
_EM = re.compile(r"(?<!\*)\*(?!\s)([^*\n]+?)(?<!\s)\*(?!\*)")
# Mistral superscript forms: $^{16}$ and ^{16}
_SUP_MATH = re.compile(r"\$\^\{?([^${}]{1,8})\}?\$")
_SUP_CARET = re.compile(r"\^\{(\w{1,4})\}")


def md_to_html(text: str) -> str:
    """Markdown styling -> HTML. Content is preserved verbatim otherwise."""
    out = html.escape(text, quote=False)
    out = _SUP_MATH.sub(r"<sup>\1</sup>", out)
    out = _SUP_CARET.sub(r"<sup>\1</sup>", out)
    out = _BOLD_EM.sub(r"<strong><em>\1</em></strong>", out)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _EM.sub(r"<em>\1</em>", out)
    return out


class _Sanitizer(HTMLParser):
    """Rebuild an HTML fragment keeping only allowlisted inline tags."""

    MAP = {
        "i": "em",
        "em": "em",
        "b": "strong",
        "strong": "strong",
        "sup": "sup",
        "sub": "sub",
        "u": "u",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: object) -> None:
        mapped = self.MAP.get(tag)
        if mapped:
            self.out.append(f"<{mapped}>")
            self.stack.append(mapped)

    def handle_endtag(self, tag: str) -> None:
        mapped = self.MAP.get(tag)
        if mapped and mapped in self.stack:
            while self.stack:  # close down to the matching tag
                top = self.stack.pop()
                self.out.append(f"</{top}>")
                if top == mapped:
                    break

    def handle_data(self, data: str) -> None:
        self.out.append(html.escape(data, quote=False))


def html_to_html(fragment: str) -> str:
    """Engine HTML (surya) -> sanitized HTML. Unknown tags are unwrapped
    (their text is kept); i/b map to em/strong."""
    p = _Sanitizer()
    p.feed(fragment)
    p.close()
    while p.stack:
        p.out.append(f"</{p.stack.pop()}>")
    return "".join(p.out).strip()
