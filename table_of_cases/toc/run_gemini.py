"""Parse table-of-cases pages with Gemini: one page image in, entries out.

Each page image is sent to the Gemini API. Output goes to
data/outputs/<engine>/page_NNNN.json; the raw response and token counts are
kept in that folder's raw/.

    uv run python -m toc.run_gemini --pages 20 --dry-run   # no API call
    uv run python -m toc.run_gemini --pages 21-40
"""

import argparse
import os
import sys

from dotenv import load_dotenv
from google import genai
from google.genai import types

from toc.config import ROOT
from toc.pages import MAX_OUTPUT_TOKENS, base_parser, run_pages
from toc.schema import RESPONSE_SCHEMAS

DEFAULT_MODEL = "gemini-4-hydrogen"


def make_extract(args: argparse.Namespace, api_key: str):
    client = genai.Client(api_key=api_key)

    def extract(image: bytes, mime_type: str, prompt: str) -> tuple[str, dict]:
        response = client.models.generate_content(
            model=args.model,
            contents=[
                types.Part.from_bytes(
                    data=image,
                    mime_type=mime_type,
                    media_resolution=args.media_resolution,
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=args.thinking_level
                ),
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMAS[args.prompt],
            ),
        )
        finish = response.candidates[0].finish_reason if response.candidates else None
        usage = response.usage_metadata
        meta = {
            "finish_reason": str(finish),
            "usage": usage.model_dump(mode="json", exclude_none=True) if usage else {},
        }
        return response.text or "", meta

    return extract


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = base_parser(__doc__.splitlines()[0], DEFAULT_MODEL)
    parser.add_argument(
        "--thinking-level", default="LOW", choices=["LOW", "MEDIUM", "HIGH", "XHIGH"]
    )
    parser.add_argument(
        "--media-resolution",
        default="MEDIA_RESOLUTION_ULTRA_HIGH",
        choices=[
            "MEDIA_RESOLUTION_LOW",
            "MEDIA_RESOLUTION_MEDIUM",
            "MEDIA_RESOLUTION_HIGH",
            "MEDIA_RESOLUTION_ULTRA_HIGH",
        ],
    )
    args = parser.parse_args()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key and not args.dry_run:
        sys.exit(f"GEMINI_API_KEY is empty. Add it to {ROOT / '.env'}.")
    settings = {
        "thinking_level": args.thinking_level,
        "media_resolution": args.media_resolution,
    }
    run_pages(args, settings, make_extract(args, api_key or "dry-run"))


if __name__ == "__main__":
    main()
