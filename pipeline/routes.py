"""The extraction routes.

dots.mocr is the MAIN OCR engine in every route. Four routes pair dots with
ONE supplemental engine and use LightOnOCR to tie-break disputed spans
(which makes their resolution a majority vote too, once LightOn joins). The
`three_way` route is fundamentally different: it runs THREE block-bbox
engines in parallel (dots + Mistral + Surya block mode), aligns their
bboxes, and resolves every dispute by direct three-way comparison — no
tiebreaker model is ever brought in. Motivation: the tiebreak path crops
small blocks, where LightOn's decoder can hallucinate (generating more text
than the crop holds) and skews math/LaTeX-heavy.

Dict order = display order in the viewer (three_way last, visually set
apart from the LightOn variants).
"""

from __future__ import annotations

from dataclasses import dataclass

MAIN_ENGINE = "dots"
TIEBREAK_ENGINE = "lighton"


@dataclass(frozen=True)
class Route:
    name: str
    # (engine, unit) per supplemental engine, run alongside dots.
    # Units: "block" (bbox + text), "page_xml" (no bboxes), "line".
    supplementals: tuple[tuple[str, str], ...]
    # None = no tiebreaker (resolution is a direct majority vote).
    tiebreak: str | None = TIEBREAK_ENGINE


ROUTES: dict[str, Route] = {
    "gemini": Route(name="gemini", supplementals=(("gemini", "page_xml"),)),
    "mistral": Route(name="mistral", supplementals=(("mistral", "block"),)),
    # Two surya variants: line mode gives fine geometry but hallucinates on
    # minimal-text crops; block mode reads with whole-page context.
    "surya_line": Route(
        name="surya_line", supplementals=(("surya", "line"),)
    ),
    "surya_block": Route(
        name="surya_block", supplementals=(("surya", "block"),)
    ),
    "three_way": Route(
        name="three_way",
        supplementals=(("mistral", "block"), ("surya", "block")),
        tiebreak=None,
    ),
}
