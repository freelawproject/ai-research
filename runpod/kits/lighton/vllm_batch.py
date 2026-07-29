"""Pod-side batched transcription — THIN, no pipeline deps. The pod does ONLY
inference: read pre-rendered crops/<key>.png + manifest.jsonl (built locally),
transcribe them through a vLLM OpenAI server, write reads/<key>.txt.
Enumeration (which crops) and arbitration (who wins) happen locally — this
script never touches the pipeline, PDFs, or engine outputs.

ONE attempt per manifest entry. Whether a read is good enough is decided
locally, by the compare stage's guards, and a read it discards comes back as a
RETRY entry in a later manifest — carrying its own `decode` settings, which
this script applies verbatim. That is why there is no retry loop here: a
budget-doubling pass makes a rambling read longer, not better.

    python vllm_batch.py --base-url http://localhost:8000/v1 --model <id> \
        --concurrency 32 [--shard 0/2] [--smoke 8]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
CROPS = HERE / "crops"
READS = HERE / "reads"
MANIFEST = HERE / "manifest.jsonl"

REP_PENALTY = 1.15
NO_REPEAT_NGRAM = 12


def _budget(area: float) -> int:
    """Token ceiling from the crop AREA, unless the entry names its own."""
    return max(128, min(1024, int(area / 500)))


async def main_async(
    base_url: str, model: str, concurrency: int, smoke: int, shard: str
):
    from openai import AsyncOpenAI

    client = AsyncOpenAI(base_url=base_url, api_key="x")
    READS.mkdir(exist_ok=True)

    crops = [
        json.loads(ln)
        for ln in MANIFEST.read_text().splitlines()
        if ln.strip()
    ]
    # every n-th crop, so the fan-out's workers own disjoint slices
    shard_i, shard_n = (int(v) for v in shard.split("/"))
    crops = crops[shard_i::shard_n]
    if smoke:
        crops = crops[:smoke]
    crops = {c["key"]: c for c in crops}
    # resume: a read already on disk is this crop's attempt, good or bad
    todo = {
        k: c for k, c in crops.items() if not (READS / f"{k}.txt").exists()
    }
    n_retry = sum(1 for c in todo.values() if c.get("decode"))
    print(
        f"{len(crops)} crops in shard {shard}, {len(todo)} to transcribe "
        f"({n_retry} retry) (model={model}, concurrency={concurrency})"
        f"{' [SMOKE]' if smoke else ''}",
        flush=True,
    )
    if not todo:
        return

    imgs = {
        k: base64.b64encode((CROPS / f"{k}.png").read_bytes()).decode()
        for k in todo
    }
    sem = asyncio.Semaphore(concurrency)

    async def one(key: str, crop: dict):
        # a retry entry names its own ceiling and brakes; everyone else gets
        # the area-scaled default
        decode = dict(crop.get("decode") or {})
        max_tokens = int(decode.pop("max_new_tokens", 0)) or _budget(
            crop["area"]
        )
        extra = {
            "repetition_penalty": REP_PENALTY,
            "no_repeat_ngram_size": NO_REPEAT_NGRAM,
            **decode,
        }
        async with sem:
            r = await client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                temperature=0.0,
                extra_body=extra,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{imgs[key]}"
                                },
                            }
                        ],
                    }
                ],
            )
        text = (r.choices[0].message.content or "").strip()
        # Written as each read lands, atomically: resume treats "the file
        # exists" as "this crop is done", so a run killed mid-batch keeps
        # every finished read and never leaves a truncated one behind.
        tmp = READS / f"{key}.txt.part"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, READS / f"{key}.txt")

    results = await asyncio.gather(
        *[one(k, c) for k, c in todo.items()], return_exceptions=True
    )
    failed = 0
    for item in results:
        if isinstance(item, BaseException):  # one crop must not end the batch
            print(f"  FAILED: {item!r}", flush=True)
            failed += 1
    print(
        f"done: {len(todo) - failed} read, {failed} failed → {READS}",
        flush=True,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="lightonai/LightOnOCR-2-1B")
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--shard", default="0/1", help="i/n — every n-th crop")
    ap.add_argument(
        "--smoke", type=int, default=0, help="only the first N crops"
    )
    a = ap.parse_args()
    asyncio.run(
        main_async(a.base_url, a.model, a.concurrency, a.smoke, a.shard)
    )
