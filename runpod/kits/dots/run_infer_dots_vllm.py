"""dots.mocr (rednote-hilab/dots.mocr, 3B) whole-page layout inference via a
vLLM OpenAI-compatible server — POD-SIDE client.

vLLM continuous-batches server-side, so throughput comes from keeping many
requests in flight (--concurrency) rather than client-side batching. Each
canonical 1700×2200 page image (the pipeline's own render, staged at pack
time — same pixels every other engine sees) is sent as a base64 PNG data
URI to /v1/chat/completions with dots' layout prompt, parsed, and written:

    out/<stem>.json = {"text": md, "regions": [{order,label,bbox,text}]}
                      (bbox in 1700×2200 space; text reassembled in reading order)

    python run_infer_dots_vllm.py --images-dir images --out-dir out \
        [--base-url http://localhost:8000/v1 --concurrency 32 \
         --max-new-tokens 12000 --model model --shard 0/1]

Resume-safe (skips existing outputs). The server is started by run.sh
(`vllm serve rednote-hilab/dots.mocr ...`, vLLM >= 0.11.0).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import time
from pathlib import Path

from openai import AsyncOpenAI

from dots_common import PROMPT_LAYOUT, parse_layout


def _png_data_uri(png: Path) -> str:
    return (
        "data:image/png;base64," + base64.b64encode(png.read_bytes()).decode()
    )


async def _infer_one(
    client, sem, model, max_tokens, images_dir, out, stem
) -> bool:
    """Read image → call vLLM → parse → write. Returns True on success."""
    async with sem:
        try:
            uri = await asyncio.to_thread(
                _png_data_uri, images_dir / f"{stem}.png"
            )
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": uri}},
                            {"type": "text", "text": PROMPT_LAYOUT},
                        ],
                    }
                ],
                temperature=0.0,
                max_tokens=max_tokens,
            )
            raw = resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001 — one bad page shouldn't kill the run
            print(f"  !! {stem}: {type(e).__name__}: {e}", flush=True)
            return False
    regions = parse_layout(raw)
    payload = (
        {
            "text": "\n".join(r["text"] for r in regions if r["text"]),
            "regions": regions,
        }
        if regions
        else {"text": raw, "regions": []}
    )
    # Atomic: resume reads "file exists" as "page done", so a run killed
    # mid-write must never leave a truncated JSON to be skipped forever.
    tmp = out / f"{stem}.json.part"
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(tmp, out / f"{stem}.json")
    return True


async def _main(args) -> None:
    images_dir, out = Path(args.images_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shard_i, shard_n = (int(v) for v in args.shard.split("/"))
    stems = sorted(
        p.stem for p in images_dir.glob("*.png") if not p.name.startswith("._")
    )
    want = {s.strip() for s in args.stems.split(",") if s.strip()}
    if want:
        stems = [s for s in stems if s in want]
    stems = stems[shard_i::shard_n]
    todo = [s for s in stems if not (out / f"{s}.json").exists()]
    print(
        f"{len(stems)} pages in shard {args.shard}, {len(todo)} to do",
        flush=True,
    )

    client = AsyncOpenAI(
        base_url=args.base_url, api_key="EMPTY", timeout=600.0
    )
    sem = asyncio.Semaphore(args.concurrency)
    t0, done, fail = time.time(), 0, 0

    async def run(stem):
        nonlocal done, fail
        ok = await _infer_one(
            client, sem, args.model, args.max_new_tokens, images_dir, out, stem
        )
        done += 1
        if not ok:
            fail += 1
        if done % 50 == 0:
            # Rate over pages actually WRITTEN. A failing server answers in
            # milliseconds, so counting failures would inflate the rate into a
            # fake speedup — an all-errors run can report tens of pg/s while
            # writing nothing.
            written = done - fail
            rate = written / (time.time() - t0) if written else 0.0
            eta = (len(todo) - done) / rate / 3600 if rate else 0
            line = (
                f"  {written}/{len(todo)} written  {rate:.2f} pg/s  "
                f"ETA {eta:.1f}h"
            )
            if fail:
                line += (
                    f"  !! {fail} FAILED — not written; re-run retries them"
                )
            print(line, flush=True)

    await asyncio.gather(*(run(s) for s in todo))
    print(
        f"done: {done - fail}/{len(todo)} pages → {out} ({fail} failed)",
        flush=True,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument(
        "--model",
        default="model",
        help="must match vllm serve --served-model-name",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=32,
        help="requests in flight; vLLM batches these server-side",
    )
    ap.add_argument("--max-new-tokens", type=int, default=12000)
    ap.add_argument("--shard", default="0/1")
    ap.add_argument(
        "--stems",
        default="",
        help="comma-separated stems to run (subset; used by the "
        "run.sh smoke test)",
    )
    args = ap.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
