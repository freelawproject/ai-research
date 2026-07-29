"""Local (no-pod) LightOn batch: the kit's input contract, run through
transformers on this machine.

    python local_batch.py <bundle-dir> [--shard i/n]

<bundle-dir> holds the standard kit input — crops/<key>.png +
manifest.jsonl ({key, expect, area} per crop) — and receives the
standard output, reads/<key>.txt. CPU float32 by default (reliable on
Apple Silicon; MPS is flaky for this VLM); a CUDA box sets
LIGHTON_DEVICE=cuda for bf16.

Same decode policy as the pod kit: greedy, repetition_penalty 1.15,
no_repeat_ngram 12, token budget scaled to the crop AREA (or the
entry's own `decode` settings on a retry crop). ONE attempt per entry —
whether a read is good enough is decided locally by the compare
stage's guards, and a discarded read comes back as a `__retry` entry
in a later bundle. Resume-safe: an existing read is skipped, and a
failed crop writes nothing, so a re-run redoes exactly the failures.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import (
    LightOnOcrForConditionalGeneration,
    LightOnOcrProcessor,
)

MODEL = "lightonai/LightOnOCR-2-1B"

REP_PENALTY = 1.15
NO_REPEAT_NGRAM = 12
TOKENS_PER_PX = 1 / 500  # tokens per crop pixel (generous headroom)
MIN_TOKENS, MAX_TOKENS = 128, 1024
MIN_SIDE = 64  # px; smaller crops are scaled up, see _readable

_state: dict = {}


def _load() -> None:
    if "model" in _state:
        return
    device = os.environ.get("LIGHTON_DEVICE", "cpu")
    dtype = torch.float32 if device == "cpu" else torch.bfloat16
    print(f"loading {MODEL} on {device} ({dtype}) …", flush=True)
    model = (
        LightOnOcrForConditionalGeneration.from_pretrained(
            MODEL, torch_dtype=dtype
        )
        .to(device)
        .eval()
    )
    proc = LightOnOcrProcessor.from_pretrained(MODEL)
    _state.update(model=model, proc=proc, device=device, dtype=dtype)


def _budget(area: float) -> int:
    return max(MIN_TOKENS, min(MAX_TOKENS, int(area * TOKENS_PER_PX)))


def _readable(png: Path, scratch: Path) -> Path:
    """The crop, scaled up when either side is below MIN_SIDE. A
    disputed region can be a single glyph — a bullet, a bracket — and
    the model merges image patches 2x2, so a crop a few pixels wide
    renders to fewer than two patches on a side and raises inside the
    image tower instead of returning a read. Crops are rendered at a
    legible minimum upstream; this keeps older bundles runnable."""
    with Image.open(png) as im:
        if min(im.size) >= MIN_SIDE:
            return png
        scale = MIN_SIDE / min(im.size)
        out = scratch / png.name
        im.resize(
            (round(im.width * scale), round(im.height * scale)),
            Image.LANCZOS,
        ).save(out)
    return out


def _transcribe(png: Path, max_new_tokens: int, decode: dict) -> str:
    _load()
    conversation = [
        {"role": "user", "content": [{"type": "image", "url": str(png)}]}
    ]
    inputs = _state["proc"].apply_chat_template(
        conversation,
        add_generation_prompt=True,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
    )
    inputs = {
        k: (
            v.to(device=_state["device"], dtype=_state["dtype"])
            if v.is_floating_point()
            else v.to(_state["device"])
        )
        for k, v in inputs.items()
    }
    with torch.no_grad():
        out = _state["model"].generate(
            **inputs,
            do_sample=False,
            **{
                "max_new_tokens": max_new_tokens,
                "repetition_penalty": REP_PENALTY,
                "no_repeat_ngram_size": NO_REPEAT_NGRAM,
                **decode,  # a retry crop carries tighter settings
            },
        )
    new = out[0, inputs["input_ids"].shape[1] :]
    return _state["proc"].decode(new, skip_special_tokens=True).strip()


def main() -> None:
    args = sys.argv[1:]
    shard = "0/1"
    if "--shard" in args:
        i = args.index("--shard")
        shard = args[i + 1]
        del args[i : i + 2]
    bundle = Path(args[0])
    manifest = bundle / "manifest.jsonl"
    crops = bundle / "crops"
    reads = bundle / "reads"
    reads.mkdir(exist_ok=True)
    entries = [
        json.loads(line)
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # every n-th crop, so N workers on one machine own disjoint slices
    shard_i, shard_n = (int(v) for v in shard.split("/"))
    entries = entries[shard_i::shard_n]
    print(f"{len(entries)} crop(s) in {bundle} (shard {shard})", flush=True)
    done = skipped = failed = 0
    t0 = time.time()
    scratch = Path(tempfile.mkdtemp(prefix="lighton_"))
    for n, e in enumerate(entries, 1):
        key = e["key"]
        out = reads / f"{key}.txt"
        if out.exists():
            skipped += 1
            continue
        png = crops / f"{key}.png"
        if not png.exists():
            print(f"[{n}] MISSING crop {key}", flush=True)
            failed += 1
            continue
        decode = e.get("decode") or {}
        budget = int(decode.pop("max_new_tokens", 0)) or _budget(
            float(e.get("area", 0))
        )
        try:
            text = _transcribe(_readable(png, scratch), budget, decode)
        except Exception as exc:  # one crop must not end the batch
            print(f"[{n}] FAILED {key}: {exc!r}", flush=True)
            failed += 1
            continue  # no read written, so a re-run retries this crop
        # atomic: resume reads "file exists" as "crop done", so a killed
        # run must never leave a truncated read to be skipped forever
        tmp = out.with_name(out.name + ".part")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out)
        done += 1
        if done % 10 == 0 or n == len(entries):
            rate = (time.time() - t0) / max(done, 1)
            left = (len(entries) - n) * rate
            print(
                f"[{n}/{len(entries)}] {done} read, {skipped} cached, "
                f"{failed} failed · {rate:.1f}s/crop · "
                f"~{left / 60:.0f}min left",
                flush=True,
            )
    print(
        f"DONE: {done} read, {skipped} already complete, {failed} failed "
        f"-> {reads}",
        flush=True,
    )


if __name__ == "__main__":
    main()
