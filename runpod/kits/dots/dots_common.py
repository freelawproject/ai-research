"""Shared dots.mocr prompt + layout-JSON parser, used by both the native
(`run_infer_dots.py`) and vLLM (`run_infer_dots_vllm.py`) runners so the prompt
and parsing stay identical across backends. Pure stdlib — no torch/vllm import."""

from __future__ import annotations

import json
import re

PROMPT_LAYOUT = (
    "Please output the layout information from the PDF image, including each "
    "layout element's bbox, its category, and the corresponding text content "
    "within the bbox.\n\n"
    "1. Bbox format: [x1, y1, x2, y2]\n\n"
    "2. Layout Categories: The possible categories are ['Caption', 'Footnote', "
    "'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', "
    "'Section-header', 'Table', 'Text', 'Title'].\n\n"
    "3. Text Extraction & Formatting Rules:\n"
    "    - Picture: For the 'Picture' category, the text field should be omitted.\n"
    "    - Formula: Format its text as LaTeX.\n"
    "    - Table: Format its text as HTML.\n"
    "    - All Others (Text, Title, etc.): Format their text as Markdown.\n\n"
    "4. Constraints:\n"
    "    - The output text must be the original text from the image, with no translation.\n"
    "    - All layout elements must be sorted according to human reading order.\n\n"
    "5. Final Output: The entire output must be a single JSON object."
)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_layout(raw: str) -> list[dict]:
    s = _FENCE.sub("", raw.strip())
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return []
    if isinstance(obj, dict):
        obj = next((v for v in obj.values() if isinstance(v, list)), [])
    if not isinstance(obj, list):
        return []
    out = []
    for i, el in enumerate(obj):
        if isinstance(el, dict):
            out.append(
                {
                    "order": i,
                    "label": el.get("category") or el.get("label"),
                    "bbox": el.get("bbox") or el.get("bounding_box"),
                    "text": el.get("text") or el.get("content") or "",
                }
            )
    return out
