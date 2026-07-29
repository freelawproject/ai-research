# Pod kits

One kit per engine. A kit packages a set's inputs, runs inference on a GPU pod,
and returns the outputs the pipeline consumes. Kits import nothing from the
pipeline, and the pod receives no PDFs, artifacts, or credentials.

## Layout

```
pack.sh              LOCAL: builds a kit tarball for one set
fanout.sh            POD-SIDE: multi-GPU fan-out, copied into every kit by pack.sh
kits/<engine>/       pod-side code — one folder per engine, no cross-imports
mistral/             the one engine with no pod: a hosted API, run locally
data/sets/<set>/     staged inputs (gitignored)
dist/                packed kits (gitignored)
```

## Contract

| | |
|---|---|
| input | `images/<page>.png`, or `crops/<key>.png` + `manifest.jsonl` for lighton |
| output | `out/<page>.json`, or `reads/<key>.txt` for lighton — one file per unit |
| resume | an existing output file means done; re-running skips it |
| scale | `GPUS=n` — one worker per card |

Kits take images, never PDFs. The canonical 1700×2200 render comes from the
pipeline's render stage, so bboxes return in the pipeline's coordinate space.

## Engines

| Kit | Model | Serving |
|---|---|---|
| `kits/dots/` | `rednote-hilab/dots.mocr` | vLLM (`run.sh`); native transformers (`run_native.sh`) |
| `kits/surya/` | `datalab-to/surya-ocr-2` | vLLM; `MODE=block` (default) or `MODE=line` |
| `kits/lighton/` | `lightonai/LightOnOCR-2-1B` | vLLM; CPU locally via `local_batch.py` |
| `kits/container_yolo/` | `freelawproject/container-yolo` | plain torch; requires REDACTED input |
| `mistral/` | Mistral OCR API | hosted; needs `MISTRAL_API_KEY` in `mistral/.env` |

## Defining a set

`data/sets/<set>/set.conf`:

| Key | Meaning |
|---|---|
| `DATASET=<name>` | use `data/datasets/<name>/page_png` as the images |
| `IMAGES=<dir>` | use a directory of PNGs instead (relative to the set) |
| `PAGES=<file>` | optional subset: one page id per line |

A lighton set holds `crops/` + `manifest.jsonl` instead — the bundle
`manage.py export_crops` writes.

## Running

```sh
# 1. define the set
mkdir -p runpod/data/sets/myset
printf 'DATASET=1k\n' > runpod/data/sets/myset/set.conf

# 2. pack
bash runpod/pack.sh surya myset          # -> runpod/dist/surya_myset_kit.tar.gz

# 3. send, run, return
runpodctl send runpod/dist/surya_myset_kit.tar.gz
#   on the pod:
tar xzf surya_myset_kit.tar.gz && cd surya
GPUS=4 bash run.sh
runpodctl send surya_out_myset.tar.gz

# 4. unpack the outputs into data/datasets/<name>/engines/<engine>/, then
uv run python -m pipeline.run --dataset <name> --route all
```

Mistral runs locally instead of on a pod — see `mistral/README.md`.

## Multi-GPU

```sh
GPUS=4 bash run.sh          # cards 0-3, shards 0/4 … 3/4
bash run.sh                 # defaults to every GPU the pod exposes
GPUS=1 bash run.sh          # one worker; also the CPU path
SKIP_PORTS=8001 GPUS=4 bash run.sh    # never probe 8001
```

Worker *i* gets card *i*, shard `i/n`, and its own server port. Workers take
every n-th unit and write into the same output directory, so there is no merge
step. The first worker installs dependencies and the rest wait for it. Each
worker logs to `worker_shard<i>of<n>.log` and its server to
`serve_shard<i>of<n>.log`; the parent reports per-shard status, tails the log of
any that failed, and packages the output once.

**Ports are probed, not assumed.** The parent walks up from `PORT` (8000), tests each port by *binding* it,
skips the ones in use, and prints the assignment. `SKIP_PORTS` excludes ports
from probing entirely. Each worker re-checks its port before serving and, if it
lost the race, stops and prints how to identify the occupant. Once the server
answers `/health` the kit also requires `/v1/models` to list the model it asked
for —
`/health` alone is satisfied by any server on that port, which is how a
collision otherwise reads as "server healthy" followed by every unit failing
with nginx's `405 Not Allowed`.

## Pod requirements

| | |
|---|---|
| GPU | 48 GB VRAM (A40 or equivalent); container_yolo also runs on CPU |
| throughput | ~3 s per page per GPU (~1,200 pages/GPU-hour) for every OCR engine: `hours ≈ pages ÷ (1200 × GPUs)` |
| disk | page images ~2.3 MB each; ~30 GB for 1,000 pages, 40–60 GB for a 3-volume set including weights and output |

**Pick a pod whose CUDA version is ≥ the CUDA the image's torch was built
for — CUDA 12.8 or newer covers every kit here.** A lower host CUDA fails at
model load; each vLLM kit preflights the driver against `torch.version.cuda`
before serving and stops with one line rather than an engine-core traceback.

| Kit | Image | Host CUDA | Other pins |
|---|---|---|---|
| dots | `vllm/vllm-openai:v0.11.0` | ≥ 12.8 | transformers `>=4.57.1,<4.58` — `run.sh` reinstalls only when out of range |
| surya | `vllm/vllm-openai:v0.20.1` | ≥ 12.8 | vLLM `>= 0.20.1` required for `Qwen3_5ForConditionalGeneration`; `ALLOW_VLLM_UPGRADE=1` installs it onto an older image |
| lighton | any recent `vllm/vllm-openai` | ≥ 12.8 | do not pin transformers |
| container_yolo | any CUDA 12.x or 13.0 image, or CPU | 12.x+ | plain torch, so the host version is not load-bearing |

`run_native.sh` (dots without vLLM) builds its own venv against `cu126` by
default — override with `DOTS_TORCH_CUDA=cu128` to match a newer host.

## Interrupted runs

Outputs are written atomically and resume is existence-based. Re-send the same
kit, unpack it over the partial output directory, and re-run; only the units in
flight are recomputed.

## Licensing

| Model | Licence |
|---|---|
| dots.mocr | MIT |
| Surya OCR 2 | code Apache 2.0; weights modified AI Pubs OpenRAIL-M (research and startups under $5M revenue; commercial licence via Datalab) |
| LightOnOCR-2-1B | Apache 2.0 |
| container-YOLO | AGPL-3.0, inherited from DocLayout-YOLO |
| Mistral OCR | proprietary API |
