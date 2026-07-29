# Surya OCR 2 pod kit

Full-page OCR with `datalab-to/surya-ocr-2` served by vLLM. Two variants, same
output shape:

- **block** (default) — one whole-page VLM call (`full_page=True`); regions are
  paragraph-level layout blocks with a block bbox + html.
- **line** — Surya's `DetectionPredictor` (EfficientViT) finds text lines, then
  the recognition VLM OCRs each line; regions are lines with line bboxes.

## Pod image — this one is not negotiable

> **`vllm/vllm-openai:v0.20.1`**

surya-ocr-2's architecture is `Qwen3_5ForConditionalGeneration`, registered only
in vLLM ≥ 0.20.1. On the dots kit's 0.11.0 image it fails with *"Model
architectures \['Qwen3_5ForConditionalGeneration'\] are not supported"*.
surya-ocr has no vLLM dependency and registers no plugin — its vLLM backend is
just an OpenAI client — so the arch has to be native to the running server.
`ALLOW_VLLM_UPGRADE=1` will pip-install 0.20.1 onto an older image. That can
trip a torch/CUDA mismatch, so booting the right image is still the clean path,
but the upgrade route is verified working and costs about 5 minutes of install at
startup. Use it when a v0.20.1 pod is not available. Under `GPUS=n` only the
deps worker runs the upgrade — the others wait on its marker and then verify,
because a second concurrent pip in the shared site-packages uninstalls
packages from under the first (how an upgrade dies halfway, leaving
`~riton`-style debris).

## Run

```sh
runpodctl receive <code>
tar xzf surya_<set>_kit.tar.gz && cd surya
bash run.sh                 # block mode
MODE=line bash run.sh       # line mode — separate out dir and tarball
runpodctl send surya_out_<set>.tar.gz
```

`run.sh` OCRs one page before the full set: the client API — especially the
line-mode detection bridge — is the main unknown on a fresh pod, and on a
multi-thousand-page set that check is the difference between losing 30 seconds
and losing an hour of GPU time.

Serve flags mirror Datalab's own launcher: `--dtype bfloat16 --max-model-len
18000 --gpu-memory-utilization 0.85 --mm-processor-kwargs
'{"min_pixels":3136,"max_pixels":6291456}' --enable-prefix-caching`. MTP
speculative decoding (a decode speedup Datalab enables by default) is off;
`ENABLE_MTP=1` matches them.

Tunables: `GPUS` (all), `MODE` (block), `PORT` (8000 — the base; workers
probe upward), `SKIP_PORTS`, `MODEL_ID`, `GPU_MEM_UTIL` (0.85),
`MAX_MODEL_LEN` (18000), `PARALLEL` (8), `BATCH` (0=auto), `ENABLE_MTP` (0),
`ALLOW_VLLM_UPGRADE` (0), `SHARD` (0/1), `SKIP_DEPS` (0). Server log →
`serve.log` (`serve_shard<i>of<n>.log` per fan-out worker).

## Two GPUs on one pod

```sh
CUDA_VISIBLE_DEVICES=0 PORT=8000 SHARD=0/2 BATCH=16 PARALLEL=16 bash run.sh
# after ">> [surya] server healthy", in a second shell:
CUDA_VISIBLE_DEVICES=1 PORT=8001 SHARD=1/2 BATCH=16 PARALLEL=16 SKIP_DEPS=1 bash run.sh
```

Or by hand, one shell per card (`GPUS=n` does exactly this for you):
stagger the starts — both `pip install`s write one site-packages, hence
`SKIP_DEPS=1` on the second. Shards share `out/` (disjoint stems, atomic
writes), so whichever run packages last tars the complete set. Data-parallel
like this beats `--tensor-parallel-size 2`, which pays cross-GPU traffic per
token without doubling pages/sec.

## Throughput

Budget **~3 s per page per GPU** (~1,200 pages per GPU-hour) and add GPUs to go
faster — see the main README's Hardware section. A40s are the recommended card.

Full-page OCR emits thousands of output tokens per page, so this is
**decode-bound**: limited by GPU memory bandwidth, not compute. That is why more
GPUs in parallel beats one faster GPU, and why `nvidia-smi` showing ~100%
utilisation tells you nothing — it counts kernels running, not saturation. The
number to watch is vLLM's own log line in `serve.log`:

```
Avg generation throughput: N tokens/s, Running: N reqs, Pending: N reqs
```

`Running: 8` with pages pending means the client is the bottleneck, not the GPU.

In-flight requests are `min(BATCH, PARALLEL)`, so **raise both or neither**:

```sh
PARALLEL=16 BATCH=16 bash run.sh                # a good default
ENABLE_MTP=1 PARALLEL=16 BATCH=16 bash run.sh   # + speculative decode
```

**Bigger is not better here, and 64 measured slower than 32.** This client is
chunk-synchronous — it submits N pages, waits for *all* N, then submits the next
N — so a chunk costs its slowest page and the GPU drains at every boundary. Page
output length varies about 5x across a volume, so wider chunks mean more finished
slots idling on one straggler. Modelled against real page-length distributions,
efficiency falls steadily from ~83% at 8 to ~72% at 128. Add GPUs to go faster,
not depth.

Re-running is resume-safe, so this can be A/B'd on the remaining pages of a
live run: Ctrl-C, re-run with new env, compare the `pg/s` line. Watch for 500s
in `serve.log` if you push depth far past the KV cache — back `BATCH` off if
the client starts falling back to per-image retries.

Line mode defaults to `--batch-size 1`: each page fans out into many per-line
requests, so batching pages on top of that floods the server with 500s.

## Contents

- `run_infer_surya.py` — client: load image → OCR → `out/<stem>.json`
- `run.sh` — serve → health → smoke → infer → tar

Input is `images/*.png`, never PDFs — pack.sh stages the pipeline's own
canonical renders, so the pod spends its time on the GPU and every engine
sees the same pixels.

## Output

```
out/<stem>.json = {stem, mode, text, regions:[{order,label,raw_label,bbox,
                   confidence,html,text}], raw}
```

Bboxes in 1700×2200 space. `raw` is the full `PageOCRResult` dump.
