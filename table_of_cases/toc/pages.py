"""Shared pieces for the per-provider runners: page images, the run loop,
and output files."""

import argparse
import json
import re
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pymupdf
from pydantic import ValidationError

from toc.config import DEFAULT_PROMPT, OUTPUTS_DIR, PROMPTS_DIR, SOURCES, page_file
from toc.schema import RESPONSE_SCHEMAS, to_page

RENDER_DPI = 300
MAX_OUTPUT_TOKENS = 32768

# (image bytes, MIME type, prompt) -> (raw response text, provider metadata)
ExtractPage = Callable[[bytes, str, str], tuple[str, dict]]


def parse_page_range(text: str) -> list[int]:
    """ "20" -> [20]; "21-40" -> [21, ..., 40]."""
    first, _, last = text.partition("-")
    return list(range(int(first), int(last or first) + 1))


def base_parser(description: str, default_model: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--pages", required=True, help="PDF page or range, e.g. 20 or 21-40"
    )
    parser.add_argument(
        "--source", choices=sorted(SOURCES), default="original", help="which scan"
    )
    parser.add_argument("--model", default=default_model)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--workers", type=int, default=5, help="pages in parallel")
    parser.add_argument(
        "--overwrite", action="store_true", help="redo pages that have output"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="prepare everything, call nothing"
    )
    return parser


def page_image(source: str, page: int) -> tuple[bytes, str]:
    """Return one page (1-based) as image bytes plus its MIME type.

    A scanned page is a single embedded image, so that image goes out
    unchanged (JPEG) or losslessly (PNG). Other pages are rendered.
    """
    with pymupdf.open(SOURCES[source]) as pdf:
        if not 1 <= page <= len(pdf):
            raise ValueError(f"Page {page} is outside 1–{len(pdf)}.")
        images = pdf[page - 1].get_images(full=True)
        if len(images) == 1:
            xref = images[0][0]
            embedded = pdf.extract_image(xref)
            if embedded["ext"] in ("jpeg", "jpg"):
                return embedded["image"], "image/jpeg"
            return pymupdf.Pixmap(pdf, xref).tobytes("png"), "image/png"
        pixmap = pdf[page - 1].get_pixmap(dpi=RENDER_DPI)
        return pixmap.tobytes("png"), "image/png"


def engine_dir(model: str, prompt_name: str, source: str) -> Path:
    version = prompt_name.rsplit("_", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]", "-", f"{model}__{version}__{source}")
    return OUTPUTS_DIR / name


def save_raw(out_dir: Path, page: int, text: str, meta: dict) -> None:
    """Keep a response and its metadata as received, usable or not."""
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"page_{page:04d}.txt").write_text(text)
    (raw_dir / f"page_{page:04d}.meta.json").write_text(json.dumps(meta, indent=2))


def save_result(out_dir: Path, page: int, text: str, meta: dict) -> str:
    """Keep the raw response and metadata, then write the viewer file.

    Raises ValueError when the response does not fit the schema; the raw
    response is kept either way.
    """
    save_raw(out_dir, page, text, meta)
    try:
        parsed = to_page(RESPONSE_SCHEMAS[meta["prompt"]].model_validate_json(text))
    except ValidationError as exc:
        raise ValueError(f"response does not fit the schema: {exc}") from exc
    record = {**meta, **parsed.model_dump()}
    page_file(out_dir, page).write_text(
        json.dumps(record, indent=2, ensure_ascii=False)
    )
    cites = sum(len(e.citations) for e in parsed.entries)
    return f"{len(parsed.entries)} entries, {cites} citations"


def run_pages(args: argparse.Namespace, settings: dict, extract: ExtractPage) -> None:
    """Run `extract` over the requested pages and write one file per page.

    Pages that already have output are skipped unless --overwrite is given,
    so re-running the same command retries only the pages that failed.
    """
    prompt = (PROMPTS_DIR / f"{args.prompt}.md").read_text()
    out_dir = engine_dir(args.model, args.prompt, args.source)
    wanted = parse_page_range(args.pages)
    todo = [p for p in wanted if args.overwrite or not page_file(out_dir, p).is_file()]
    described = " · ".join(f"{k} {v}" for k, v in settings.items())
    print(f"model {args.model} · {described} · source {args.source}")
    print(f"{len(todo)} of {len(wanted)} pages to run → {out_dir}")
    if args.dry_run:
        if todo:
            image, mime_type = page_image(args.source, todo[0])
            print(f"page {todo[0]}: {len(image) / 1e6:.1f} MB {mime_type}")
        print("dry run: no API call made.")
        return

    def one(page: int) -> str | None:
        """Process one page; return an error message, or None on success."""
        try:
            image, mime_type = page_image(args.source, page)
            started = time.monotonic()
            text, provider_meta = extract(image, mime_type, prompt)
            meta = {
                "page": page,
                "source": args.source,
                "model": args.model,
                "prompt": args.prompt,
                **settings,
                "seconds": round(time.monotonic() - started, 1),
                **provider_meta,
            }
            print(f"page {page}: {save_result(out_dir, page, text, meta)}")
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            return f"page {page}: {exc}"
        return None

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        failures = [message for message in pool.map(one, todo) if message]
    if failures:
        print(f"\n{len(failures)} page(s) failed; run the same command to retry:")
        print("\n".join(failures))
        sys.exit(1)
