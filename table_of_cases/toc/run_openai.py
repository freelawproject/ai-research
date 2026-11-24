"""Parse table-of-cases pages with an OpenAI model: one page image in,
entries out.

Each page image is sent to the OpenAI API. Same prompt, schema and output
files as run_gemini. The key is read from OPENAI_KEY or OPENAI_API_KEY.

    uv run python -m toc.run_openai --pages 20 --dry-run   # no API call
    uv run python -m toc.run_openai --pages 21-40
"""

import argparse
import base64
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from toc.config import ROOT
from toc.pages import MAX_OUTPUT_TOKENS, base_parser, run_pages
from toc.schema import RESPONSE_SCHEMAS

DEFAULT_MODEL = "gpt-6.2-sol"


def make_extract(args: argparse.Namespace, api_key: str):
    client = OpenAI(api_key=api_key)

    def extract(image: bytes, mime_type: str, prompt: str) -> tuple[str, dict]:
        data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"
        response = client.responses.parse(
            model=args.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": args.detail,
                        },
                        {"type": "input_text", "text": prompt},
                    ],
                }
            ],
            reasoning={"effort": args.reasoning_effort},
            max_output_tokens=MAX_OUTPUT_TOKENS,
            text_format=RESPONSE_SCHEMAS[args.prompt],
        )
        usage = response.usage
        meta = {
            "finish_reason": response.status,
            "incomplete_details": (
                response.incomplete_details.model_dump()
                if response.incomplete_details
                else None
            ),
            "usage": usage.model_dump(mode="json") if usage else {},
        }
        return response.output_text or "", meta

    return extract


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = base_parser(__doc__.splitlines()[0], DEFAULT_MODEL)
    parser.add_argument(
        "--reasoning-effort", default="low", choices=["low", "medium", "high"]
    )
    parser.add_argument(
        "--detail", default="original", choices=["low", "high", "original", "auto"]
    )
    args = parser.parse_args()
    api_key = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY") or ""
    if not api_key and not args.dry_run:
        sys.exit("No OpenAI key. Set OPENAI_API_KEY in .env or the environment.")
    settings = {"reasoning_effort": args.reasoning_effort, "detail": args.detail}
    run_pages(args, settings, make_extract(args, api_key or "dry-run"))


if __name__ == "__main__":
    main()
