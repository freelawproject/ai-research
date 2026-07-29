"""Mistral OCR API layer: realtime `/v1/ocr` and the BATCH API.

Scalable shape (same at 30 or 1000 pages): upload each rendered page PNG
once as an `ocr` file and reference it by id in the JSONL, so the batch
manifest stays tiny (file ids, not inlined base64). One job per call;
`include_blocks=True` per line so paragraph bboxes come back (1700×2200
space, since the PNG is the pipeline's canonical render).

    from mistral_ocr import run_ocr_batch
    results = run_ocr_batch([(stem, png_bytes), ...])   # {stem: {markdown, blocks}}
"""

from __future__ import annotations

import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

MODEL = os.environ.get("MISTRAL_MODEL", "mistral-ocr-latest")
_TERMINAL = {"SUCCESS", "FAILED", "TIMEOUT_EXCEEDED", "CANCELLED"}


def _client():
    try:
        from mistralai import Mistral
    except ImportError:  # 2.x namespace layout
        from mistralai.client import Mistral
    key = os.environ.get("MISTRAL_API_KEY")
    if not key:
        raise RuntimeError("MISTRAL_API_KEY not set (see .env)")
    return Mistral(api_key=key)


def parse(d: dict) -> dict:
    """Mistral OCR response dict → {markdown, blocks[{type,bbox,text}]}."""
    markdown = "\n".join((p.get("markdown") or "") for p in d.get("pages", []))
    blocks = []
    for p in d.get("pages", []):
        for b in p.get("blocks") or []:
            if b.get("top_left_x") is None:
                continue
            blocks.append(
                {
                    "type": b.get("type"),
                    "bbox": [
                        round(b["top_left_x"], 1),
                        round(b["top_left_y"], 1),
                        round(b["bottom_right_x"], 1),
                        round(b["bottom_right_y"], 1),
                    ],
                    "text": (b.get("content") or "").strip(),
                }
            )
    return {"markdown": markdown.strip(), "blocks": blocks}


def run_ocr_realtime(
    items: list[tuple[str, bytes]], concurrency: int = 8, log=print
) -> dict[str, dict]:
    """Synchronous `/v1/ocr` per page (image as data URI, include_blocks), run
    across a small thread pool. Same {stem: {markdown, blocks}} shape as the
    batch path. Use when the batch queue is too slow."""
    if not items:
        return {}
    cli = _client()

    def one(stem: str, png: bytes) -> tuple[str, dict]:
        b64 = base64.b64encode(png).decode()
        doc = {
            "type": "image_url",
            "image_url": f"data:image/png;base64,{b64}",
        }
        last = ""
        for attempt in range(5):  # retry transient errors (429/5xx)
            try:
                resp = cli.ocr.process(
                    model=MODEL,
                    document=doc,
                    include_blocks=True,
                    table_format="html",
                )
                d = resp.model_dump() if hasattr(resp, "model_dump") else resp
                return stem, parse(d)
            except Exception as e:
                last = str(e)[:200]
                time.sleep(min(2**attempt, 30))  # 1,2,4,8,16 → cap 30s
        return stem, {"markdown": "", "blocks": [], "error": last}

    out: dict[str, dict] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(one, s, p) for s, p in items]
        for f in as_completed(futs):
            stem, res = f.result()
            out[stem] = res
            done += 1
            if done % 25 == 0 or done == len(items):
                log(f"    {done}/{len(items)}")
    return out


def _download(cli, file_id: str) -> bytes:
    resp = cli.files.download(file_id=file_id)
    if hasattr(resp, "read"):
        return resp.read()
    if isinstance(resp, (bytes, bytearray)):
        return bytes(resp)
    return b"".join(resp)


def run_ocr_batch(
    items: list[tuple[str, bytes]], poll_s: int = 20, log=print
) -> dict[str, dict]:
    """items = [(stem, png_bytes)]. Returns {stem: {markdown, blocks}} (or
    {..., error} for a failed line). One batch job over all items."""
    if not items:
        return {}
    cli = _client()

    log(f"  uploading {len(items)} page images as ocr files…")
    file_ids = {}
    for i, (stem, png) in enumerate(items, 1):
        up = cli.files.upload(
            file={"file_name": f"{stem}.png", "content": png}, purpose="ocr"
        )
        file_ids[stem] = up.id
        if i % 100 == 0:
            log(f"    uploaded {i}/{len(items)}")

    lines = [
        json.dumps(
            {
                "custom_id": stem,
                "body": {
                    "model": MODEL,
                    "document": {"type": "file", "file_id": fid},
                    "include_blocks": True,
                    "table_format": "html",
                },
            }
        )
        for stem, fid in file_ids.items()
    ]
    manifest = cli.files.upload(
        file={
            "file_name": "ocr_batch.jsonl",
            "content": ("\n".join(lines)).encode(),
        },
        purpose="batch",
    )

    job = cli.batch.jobs.create(
        endpoint="/v1/ocr", input_files=[manifest.id], model=MODEL
    )
    log(f"  batch job {job.id} created; polling…")
    while job.status not in _TERMINAL:
        time.sleep(poll_s)
        job = cli.batch.jobs.get(job_id=job.id)
        done = (getattr(job, "succeeded_requests", 0) or 0) + (
            getattr(job, "failed_requests", 0) or 0
        )
        log(
            f"    status={job.status} {done}/{getattr(job, 'total_requests', '?')}"
        )
    if not getattr(job, "output_file", None):
        raise RuntimeError(f"batch job {job.id} ended {job.status}, no output")

    out: dict[str, dict] = {}
    raw = _download(cli, job.output_file).decode("utf-8", "replace")
    for line in raw.splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cid = rec.get("custom_id")
        body = (rec.get("response") or {}).get("body") or rec.get("body") or {}
        out[cid] = (
            {"markdown": "", "blocks": [], "error": rec.get("error")}
            if rec.get("error") or not body
            else parse(body)
        )
    return out
