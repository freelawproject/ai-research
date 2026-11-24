"""Run table-of-cases pages through the OpenAI Batch API (half price, results
within 24 hours).

Each page image is sent to the OpenAI API, in the same request the
synchronous runner (toc.run_openai) makes. The work is split into several
batches because one batch file may not exceed 200 MB.

    uv run python -m toc.batch_openai prepare --pages 41-1336   # no API call
    uv run python -m toc.batch_openai submit      # upload and start
    uv run python -m toc.batch_openai status      # one status call per batch
    uv run python -m toc.batch_openai collect     # download, write outputs

`prepare` skips pages that already have output, so after `collect` the same
steps retry whatever failed. State lives in data/batches/<engine>/: the
request files, `manifest.json` (earlier manifests are kept beside it), and
every downloaded result file.
"""

import argparse
import base64
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Literal

import pymupdf
from dotenv import load_dotenv
from openai import OpenAI
from openai.lib._parsing._responses import type_to_text_format_param

from toc.config import DATA_DIR, DEFAULT_PROMPT, PROMPTS_DIR, ROOT, SOURCES, page_file
from toc.pages import (
    MAX_OUTPUT_TOKENS,
    engine_dir,
    page_image,
    parse_page_range,
    save_raw,
    save_result,
)
from toc.schema import RESPONSE_SCHEMAS

DEFAULT_MODEL = "gpt-6.2-sol"
BATCHES_DIR = DATA_DIR / "batches"
ENDPOINT: Literal["/v1/responses"] = "/v1/responses"
# The API refuses batch files over 200 MB; stay clear of it.
MAX_CHUNK_BYTES = 180_000_000
TERMINAL = ("completed", "failed", "expired", "cancelled")


def request_body(
    model: str, prompt_name: str, effort: str, detail: str, image: bytes, mime_type: str
) -> dict:
    """The /v1/responses request for one page (same as the synchronous runner)."""
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode()}"
    prompt = (PROMPTS_DIR / f"{prompt_name}.md").read_text()
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_image", "image_url": data_url, "detail": detail},
                    {"type": "input_text", "text": prompt},
                ],
            }
        ],
        "reasoning": {"effort": effort},
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "text": {"format": type_to_text_format_param(RESPONSE_SCHEMAS[prompt_name])},
    }


def custom_id(page: int) -> str:
    return f"page-{page:04d}"


def page_of(request_id: str) -> int:
    return int(request_id.removeprefix("page-"))


def response_text(body: dict) -> str:
    """The model's text from a Response object, as plain JSON text."""
    parts = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                parts.append(content["text"])
    return "".join(parts)


def read_result_line(line: str) -> tuple[int, dict | None, str | None]:
    """One line of a batch output or error file.

    Returns (page, Response body or None, problem or None). The body is
    None when the request itself failed.
    """
    item = json.loads(line)
    page = page_of(item["custom_id"])
    response = item.get("response") or {}
    body = response.get("body") or {}
    if item.get("error") or response.get("status_code") != 200:
        return page, None, f"request failed: {item.get('error') or body.get('error')}"
    if body.get("status") != "completed":
        detail = body.get("incomplete_details")
        return page, body, f"response {body.get('status')}: {detail}"
    return page, body, None


def state_dir(args: argparse.Namespace) -> Path:
    return BATCHES_DIR / engine_dir(args.model, args.prompt, args.source).name


def load_manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    if not path.is_file():
        sys.exit(f"Nothing prepared in {directory}. Run `prepare` first.")
    return json.loads(path.read_text())


def save_manifest(directory: Path, manifest: dict) -> None:
    """Write the manifest in one step, so a crash cannot leave half a file."""
    tmp = directory / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, directory / "manifest.json")


def client() -> OpenAI:
    api_key = os.getenv("OPENAI_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        sys.exit("No OpenAI key. Set OPENAI_API_KEY in .env or the environment.")
    return OpenAI(api_key=api_key)


def prepare(args: argparse.Namespace) -> None:
    """Write the batch input files for every requested page without output."""
    directory = state_dir(args)
    old_manifest = directory / "manifest.json"
    if old_manifest.is_file():
        chunks = load_manifest(directory)["chunks"]
        waiting = [
            c for c in chunks if c.get("input_file_id") and not c.get("collected")
        ]
        if waiting:
            sys.exit("A submitted batch has not been collected yet. Run `collect`.")
    wanted = parse_page_range(args.pages)
    with pymupdf.open(SOURCES[args.source]) as pdf:
        if not wanted or wanted[0] < 1 or wanted[-1] > len(pdf):
            sys.exit(f"--pages {args.pages} is outside 1–{len(pdf)}.")
    out_dir = engine_dir(args.model, args.prompt, args.source)
    todo = [p for p in wanted if not page_file(out_dir, p).is_file()]
    directory.mkdir(parents=True, exist_ok=True)

    # Build under temporary names; the old files go only once the new are whole.
    chunks = []
    handle, size = None, 0
    for page in todo:
        image, mime_type = page_image(args.source, page)
        body = request_body(
            args.model,
            args.prompt,
            args.reasoning_effort,
            args.detail,
            image,
            mime_type,
        )
        request = {
            "custom_id": custom_id(page),
            "method": "POST",
            "url": ENDPOINT,
            "body": body,
        }
        line = json.dumps(request) + "\n"  # ASCII, so characters equal bytes
        if handle is None or size + len(line) > MAX_CHUNK_BYTES:
            if handle:
                handle.close()
            name = f"chunk_{len(chunks) + 1:03d}.jsonl"
            handle, size = (directory / f"{name}.new").open("w"), 0
            chunks.append({"file": name, "pages": []})
        handle.write(line)
        size += len(line)
        chunks[-1]["pages"].append(page)
    if handle:
        handle.close()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if old_manifest.is_file():
        os.replace(old_manifest, directory / f"manifest_{stamp}.json")
    for old in directory.glob("chunk_*.jsonl"):
        old.unlink()
    for chunk in chunks:
        os.replace(directory / f"{chunk['file']}.new", directory / chunk["file"])
    save_manifest(
        directory,
        {
            "prepared": stamp,
            "model": args.model,
            "prompt": args.prompt,
            "source": args.source,
            "reasoning_effort": args.reasoning_effort,
            "detail": args.detail,
            "chunks": chunks,
        },
    )
    megabytes = sum((directory / c["file"]).stat().st_size for c in chunks) / 1e6
    done = len(wanted) - len(todo)
    print(f"{len(todo)} of {len(wanted)} pages need a run ({done} already done)")
    print(f"{len(chunks)} batch file(s), {megabytes:,.0f} MB, in {directory}")
    print("No API call made. Next: submit")


def submit(args: argparse.Namespace) -> None:
    """Upload each batch file and start its batch. Safe to re-run: batches
    already started are skipped."""
    directory = state_dir(args)
    manifest = load_manifest(directory)
    api = client()
    started = 0
    for chunk in manifest["chunks"]:
        if chunk.get("batch_id"):
            continue
        if chunk.get("input_file_id") and not args.resubmit:
            # The upload was recorded but no batch id: the earlier run stopped
            # in between, and the batch may or may not exist.
            sys.exit(
                f"{chunk['file']} was uploaded but its batch was not recorded. "
                "Check the OpenAI dashboard for a batch on file "
                f"{chunk['input_file_id']}. If there is one, add its id to the "
                "manifest as batch_id; if not, run submit --resubmit."
            )
        if not chunk.get("input_file_id"):
            with (directory / chunk["file"]).open("rb") as handle:
                upload = api.files.create(file=handle, purpose="batch")
            chunk["input_file_id"] = upload.id
            save_manifest(directory, manifest)
        # No automatic retry: a repeated create would start a second batch.
        batch = api.with_options(max_retries=0).batches.create(
            input_file_id=chunk["input_file_id"],
            endpoint=ENDPOINT,
            completion_window="24h",
            metadata={"engine": directory.name, "chunk": chunk["file"]},
        )
        chunk.update(batch_id=batch.id, status=batch.status)
        save_manifest(directory, manifest)
        started += 1
        first, last = chunk["pages"][0], chunk["pages"][-1]
        print(f"{chunk['file']}: pages {first}–{last} → {batch.id} ({batch.status})")
    print(f"{started} batch(es) started. Next: status, then collect.")


def refresh(api: OpenAI, chunk: dict) -> None:
    """Fetch a batch's current state into its manifest entry."""
    batch = api.batches.retrieve(chunk["batch_id"])
    counts = batch.request_counts
    errors = batch.errors.data if batch.errors and batch.errors.data else []
    chunk.update(
        status=batch.status,
        output_file_id=batch.output_file_id,
        error_file_id=batch.error_file_id,
        completed=counts.completed if counts else None,
        failed=counts.failed if counts else None,
        errors=[f"{e.code}: {e.message}" for e in errors],
    )


def status(args: argparse.Namespace) -> None:
    directory = state_dir(args)
    manifest = load_manifest(directory)
    api = client()
    for chunk in manifest["chunks"]:
        if not chunk.get("batch_id"):
            print(f"{chunk['file']}: not submitted")
            continue
        if not chunk.get("collected"):
            refresh(api, chunk)
            save_manifest(directory, manifest)
        done, total = chunk.get("completed"), len(chunk["pages"])
        print(
            f"{chunk['file']}: {chunk['status']} · {done}/{total} done · "
            f"{chunk.get('failed')} failed"
        )
        for error in chunk.get("errors") or []:
            print(f"    {error}")
    finished = all(c.get("status") in TERMINAL for c in manifest["chunks"])
    print("All batches finished. Next: collect" if finished else "Still running.")


def collect_chunk(api: OpenAI, directory: Path, manifest: dict, chunk: dict) -> int:
    """Download one finished batch and write its pages. Returns pages written.

    Pages that cannot be used are listed in chunk["problems"]; their raw
    response, if any, is kept so the reason can be read later.
    """
    out_dir = engine_dir(manifest["model"], manifest["prompt"], manifest["source"])
    problems: dict[int, str] = {}
    written = set()
    for kind in ("output_file_id", "error_file_id"):
        if not chunk.get(kind):
            continue
        text = api.files.content(chunk[kind]).content.decode("utf-8")
        stem = chunk["file"].removesuffix(".jsonl")
        (directory / f"{stem}.{kind.removesuffix('_file_id')}.jsonl").write_text(text)
        for line in text.split("\n"):
            if not line.strip():
                continue
            try:
                page, body, problem = read_result_line(line)
            except Exception as exc:  # noqa: BLE001 - one bad line must not stop the rest
                problems[-len(problems) - 1] = f"unreadable result line: {exc}"
                continue
            if body is None:
                problems[page] = problem or "request failed"
                continue
            meta = {
                "page": page,
                "source": manifest["source"],
                "model": manifest["model"],
                "prompt": manifest["prompt"],
                "reasoning_effort": manifest["reasoning_effort"],
                "detail": manifest["detail"],
                "batch": True,
                "finish_reason": body.get("status"),
                "incomplete_details": body.get("incomplete_details"),
                "usage": body.get("usage") or {},
            }
            try:
                answer = response_text(body)
                if problem:
                    save_raw(out_dir, page, answer, meta)
                    problems[page] = problem
                    continue
                summary = save_result(out_dir, page, answer, meta)
            except Exception as exc:  # noqa: BLE001 - report the page, keep going
                problems[page] = str(exc)
                continue
            written.add(page)
            if summary.startswith("0 entries"):
                print(f"    note: page {page} came back with no entries")
    for page in chunk["pages"]:
        if page not in written and page not in problems:
            problems[page] = "no result returned"
    chunk["problems"] = [
        f"page {page}: {text}" if page > 0 else text
        for page, text in sorted(problems.items())
    ]
    chunk["collected"] = True
    return len(written)


def collect(args: argparse.Namespace) -> None:
    """Download finished batches and write one output file per page."""
    directory = state_dir(args)
    manifest = load_manifest(directory)
    api = client()
    written = 0
    for chunk in manifest["chunks"]:
        if chunk.get("collected") or not chunk.get("batch_id"):
            continue
        refresh(api, chunk)  # never trust a status seen earlier
        if chunk["status"] in TERMINAL:
            written += collect_chunk(api, directory, manifest, chunk)
        save_manifest(directory, manifest)
    running = [c["file"] for c in manifest["chunks"] if not c.get("collected")]
    problems = [p for c in manifest["chunks"] for p in c.get("problems") or []]
    for chunk in manifest["chunks"]:
        for error in chunk.get("errors") or []:
            print(f"{chunk['file']} ({chunk.get('status')}): {error}")
    print(f"{written} page(s) written this time")
    if running:
        print(f"{len(running)} batch(es) not finished yet: run collect again later.")
    if problems:
        print(f"\n{len(problems)} page(s) need a retry (prepare, then submit again):")
        print("\n".join(problems))


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["prepare", "submit", "status", "collect"])
    parser.add_argument("--pages", help="PDF page or range, e.g. 41-1336 (prepare)")
    parser.add_argument("--source", choices=sorted(SOURCES), default="original")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument(
        "--reasoning-effort", default="low", choices=["low", "medium", "high"]
    )
    parser.add_argument(
        "--detail", default="original", choices=["low", "high", "original", "auto"]
    )
    parser.add_argument(
        "--resubmit",
        action="store_true",
        help="submit: start a batch for a file whose earlier batch was not recorded",
    )
    args = parser.parse_args()
    if args.command == "prepare" and not args.pages:
        parser.error("prepare needs --pages")
    {"prepare": prepare, "submit": submit, "status": status, "collect": collect}[
        args.command
    ](args)


if __name__ == "__main__":
    main()
