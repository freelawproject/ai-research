"""dots.mocr (rednote-hilab/dots.mocr, 3B) whole-page layout inference —
STANDALONE native-transformers runner (the no-vLLM fallback), single
variant. Reads the canonical 1700×2200 page images (the pipeline's own
renders, staged at pack time — same space as every other engine) and runs
dots' layout-all prompt:

    out/<stem>.json = {"text": md, "regions": [{order,label,bbox,text}]}
                      (bbox in 1700×2200 space; text reassembled in reading order)

    python run_infer_dots.py --images-dir images --out-dir out \
        [--device auto --max-new-tokens 12000 --batch-size 4 --shard 0/1]

Pages are batched (--batch-size) through one model.generate() call to keep a
big-memory GPU busy; a batch finishes when its SLOWEST page hits EOS, so
--max-new-tokens is capped to bound any runaway-decode page.

Resume-safe; --shard i/n for multi-GPU. Env (torch 2.7 / transformers 4.57.6 /
flash-attn / qwen-vl-utils, isolated venv) is set up by run_native.sh per the
dots authors' spec. The repo name's '.' breaks trust_remote_code, so the model
is materialized in a no-period local dir first (see _local_model_dir).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import torch

from dots_common import PROMPT_LAYOUT, parse_layout

MODEL = "rednote-hilab/dots.mocr"


def _local_model_dir() -> str:
    """dots.mocr's repo name contains a '.', which breaks trust_remote_code's
    relative imports; materialize the snapshot in a NO-PERIOD local dir and load
    from that path (official workaround). Idempotent."""
    from huggingface_hub import snapshot_download

    local = str(Path(__file__).resolve().parent / "dots_mocr_model")
    snapshot_download(MODEL, local_dir=local)
    return local


def _load():
    from transformers import AutoModelForCausalLM, AutoProcessor

    local = _local_model_dir()
    try:
        model = AutoModelForCausalLM.from_pretrained(
            local,
            attn_implementation="flash_attention_2",
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        ).eval()
    except Exception:
        model = AutoModelForCausalLM.from_pretrained(
            local,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        ).eval()
    proc = AutoProcessor.from_pretrained(
        local, trust_remote_code=True, use_fast=True
    )
    return model, proc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", default="images")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--max-new-tokens", type=int, default=12000)
    ap.add_argument(
        "--batch-size",
        type=int,
        default=4,
        help="pages per generate() call — fills a big-memory GPU",
    )
    ap.add_argument("--shard", default="0/1")
    args = ap.parse_args()

    from qwen_vl_utils import process_vision_info

    images_dir, out = Path(args.images_dir), Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shard_i, shard_n = (int(v) for v in args.shard.split("/"))
    stems = sorted(
        p.stem for p in images_dir.glob("*.png") if not p.name.startswith("._")
    )[shard_i::shard_n]
    todo = [s for s in stems if not (out / f"{s}.json").exists()]
    print(
        f"{len(stems)} pages in shard {args.shard}, {len(todo)} to do",
        flush=True,
    )

    model, proc = _load()
    # left-pad so batched sequences share a trailing input length → the newly
    # generated tokens sit at a common offset for a clean batched slice/decode.
    proc.tokenizer.padding_side = "left"

    def generate_batch(png_paths: list[Path]) -> list[str]:
        msgs = [
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": str(p)},
                        {"type": "text", "text": PROMPT_LAYOUT},
                    ],
                }
            ]
            for p in png_paths
        ]
        texts = [
            proc.apply_chat_template(
                m, tokenize=False, add_generation_prompt=True
            )
            for m in msgs
        ]
        image_inputs, video_inputs = process_vision_info(msgs)
        inputs = proc(
            text=texts,
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(model.device)
        with torch.no_grad():
            gen = model.generate(
                **inputs, max_new_tokens=args.max_new_tokens, do_sample=False
            )
        trimmed = gen[
            :, inputs.input_ids.shape[1] :
        ]  # left-padded → shared offset
        return proc.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )

    def batches(seq, n):
        for i in range(0, len(seq), n):
            yield seq[i : i + n]

    t0, done = time.time(), 0
    for chunk in batches(todo, args.batch_size):
        pngs = [images_dir / f"{stem}.png" for stem in chunk]
        raws = generate_batch(pngs)
        for stem, raw in zip(chunk, raws):
            regions = parse_layout(raw)
            payload = (
                {
                    "text": "\n".join(r["text"] for r in regions if r["text"]),
                    "regions": regions,
                }
                if regions
                else {"text": raw, "regions": []}
            )
            # Atomic: resume reads "file exists" as "page done", so a run
            # killed mid-write must never leave a truncated JSON behind.
            tmp = out / f"{stem}.json.part"
            tmp.write_text(json.dumps(payload, ensure_ascii=False))
            os.replace(tmp, out / f"{stem}.json")
            done += 1
        rate = done / (time.time() - t0)
        eta = (len(todo) - done) / rate / 3600 if rate else 0
        print(
            f"  {done}/{len(todo)} written  {rate:.2f} pg/s  ETA {eta:.1f}h",
            flush=True,
        )
    print(f"done: {done} pages → {out}", flush=True)


if __name__ == "__main__":
    main()
