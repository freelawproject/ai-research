# dots.mocr pod kit

Whole-page layout + OCR with `rednote-hilab/dots.mocr` (3B). Reads the
canonical 1700×2200 page images and runs dots' layout-all prompt — one
variant, no redaction fills, no block mode.

Default backend is **vLLM** (throughput); a native-transformers path is kept as
a fallback.

## Pod image

**`vllm/vllm-openai:v0.11.0`** — vLLM preinstalled, and dots.mocr is officially
integrated as of 0.11.0, so no custom registration. Any CUDA 12.x image works
too; `run.sh` installs `vllm==0.11.0` if it is missing.

Two install-order gotchas `run.sh` already handles:

- **transformers must be in the 4.57 series, but not 4.57.0.** vLLM 0.11.0
  needs 4.57.x and its dep spec has no upper cap, so a base image's
  transformers 5.x survives the vllm install and breaks the tokenizer
  (`Qwen2Tokenizer.all_special_tokens_extended`). `run.sh` checks the
  installed version and reinstalls only when it is out of range (4.57.0
  itself is yanked on PyPI — hence the `.1` floor).
- **hf_transfer must be present.** Base images set
  `HF_HUB_ENABLE_HF_TRANSFER=1`, which hard-fails the ~6 GB model download
  unless the package is installed.

## Run

```sh
runpodctl receive <code>
tar xzf dots_<set>_kit.tar.gz && cd dots
bash run.sh
runpodctl send dots_out_<set>.tar.gz
```

`run.sh` serves:

```sh
vllm serve rednote-hilab/dots.mocr --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.9 --chat-template-content-format string \
  --served-model-name model --trust-remote-code
```

then does one page as a smoke test and runs the rest at `--concurrency 32`.
vLLM continuous-batches those server-side — that is where the throughput comes
from, not client-side batching.

Tunables: `GPUS` (all), `PORT` (8000 — the base; workers probe upward),
`SKIP_PORTS`, `CONCURRENCY` (32), `MODEL_ID`, `GPU_MEM_UTIL` (0.9),
`SHARD` (0/1). Server log → `serve.log` (`serve_shard<i>of<n>.log` per
fan-out worker).

## Throughput

Budget **~3 s per page per GPU** (~1,200 pages per GPU-hour). Whole-page OCR is
decode-bound — limited by GPU memory bandwidth rather than compute — so to go
faster, add GPUs (`GPUS=n bash run.sh`, one worker per card) rather than buying
a card with more compute. A40s are the recommended card; see the main README's
Hardware section for choosing a GPU count from a deadline.

### Native transformers fallback

```sh
bash run_native.sh              # isolated uv venv (torch 2.7 / transformers 4.57.6 / flash-attn)
SHARD=0/2 bash run_native.sh    # or shard across GPUs, one process each —
                                # start the second shell only after the
                                # first one's install finishes (they share
                                # .dots-venv; concurrent installs corrupt it)
```

Batched with a `--max-new-tokens` cap (12000) to bound runaway-decode pages.
Materialises the model in a no-period directory (`dots_mocr_model/`) so
`trust_remote_code` imports resolve.

## Contents

- `run_infer_dots_vllm.py` — async client: image → base64 PNG → vLLM → parse
- `run_infer_dots.py` — native transformers runner (batched); fallback
- `dots_common.py` — shared prompt + layout-JSON parser, used by both runners
- `run.sh` / `run_native.sh` — orchestrators

## Known issue — sparse pages can time out

Rarely (roughly one page in two thousand, always unusually sparse ones), a
page times out repeatedly and never produces output: the model runs to the
`--max-new-tokens` cap, which outlasts the client's 600 s timeout, and
retrying does not help. The client logs the failure and moves on, so the run
completes and the page is simply absent from `out/` — a missing page is
never treated as an error, by design. Check coverage afterwards (compare
`ls out/*.json | wc -l` against the set's page count); when a gap matters,
the content is available from another engine rather than by retrying dots.

## Output

```
out/<stem>.json = {"text": md, "regions": [{order,label,bbox,text}]}
```

Bboxes in 1700×2200 space; `text` is the regions reassembled in reading order.
