"""The three extraction routes.

Every route uses dots.mocr as the main OCR engine and LightOnOCR as the
tiebreaker on disputed spans; only the supplemental engine differs.
"""

from dataclasses import dataclass

MAIN_ENGINE = "dots"
TIEBREAK_ENGINE = "lighton"


@dataclass(frozen=True)
class Route:
    name: str
    supplemental: str
    # Shape of the supplemental engine's output:
    # "block" (bbox + text), "page_xml" (no bboxes), "line" (bbox + text).
    supplemental_unit: str


ROUTES: dict[str, Route] = {
    "gemini": Route(
        name="gemini", supplemental="gemini", supplemental_unit="page_xml"
    ),
    "mistral": Route(
        name="mistral", supplemental="mistral", supplemental_unit="block"
    ),
    "surya": Route(
        name="surya", supplemental="surya", supplemental_unit="line"
    ),
}
