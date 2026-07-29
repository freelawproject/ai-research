# LightOn pod kit

Tiebreak transcription with `lightonai/LightOnOCR-2-1B` served by vLLM.

**This kit does not take pages.** Where the other kits OCR a whole set, LightOn
re-reads the small regions the pipeline could not agree on. Enumeration (which
regions are disputed) and arbitration (whose reading wins) both stay local; the
pod does inference and nothing else, which is why `vllm_batch.py` imports no
pipeline code.

## Input contract

| | |
|---|---|
| `crops/<key>.png` | the disputed region, cropped from the 1700×2200 page render |
| `manifest.jsonl` | one `{key, expect, area}` per crop, `decode` on a retry |

`expect` is the main engine's reading of that region — used only as a
completeness check, never as a hint to the model. `area` sizes the token
budget. `key` is free-form; the local side uses
`<page-stem>_<x1>_<y1>_<x2>_<y2>` so a read maps back to its bbox.

The extraction package emits exactly this bundle:
`manage.py export_crops --dataset <name>` writes it to
`data/exports/lighton_<name>/` (every disputed region without a cached
read). Stage the bundle into `data/sets/<set>/`, then
`bash runpod/pack.sh lighton <set>`; feed the reads back with the
package's `manage.py ingest_reads`.

**One attempt per entry.** Whether a read is usable is decided locally, by
the pipeline's guards — not here. A read the pipeline discards comes back as a
RETRY entry in a later bundle: its key ends in `__retry` and it carries a
`decode` object (`max_new_tokens`, `repetition_penalty`,
`no_repeat_ngram_size`) that both runners apply verbatim in place of their
defaults. Retry settings are deliberately TIGHTER than the first attempt — the
failure being corrected is usually a model that rambled, and more room makes
that worse. A retry bundle looks like any other bundle to this kit; nothing
extra to pass.

## Local run (no pod)

For bounded bundles, `local_batch.py` runs the same job on this machine
— same input contract, same output, same decode policy (area-scaled
budget or the entry's own, repetition guards), resume-safe:

```sh
python local_batch.py <bundle-dir>     # needs torch + transformers
```

CPU float32 by default (reliable on Apple Silicon — MPS is flaky for
this VLM; ~9 s/crop); `LIGHTON_DEVICE=cuda` on a GPU box for bf16.
Verified to reproduce the pod's reads byte-for-byte on cached crops.

## Pod image

A recent `vllm/vllm-openai`. **Do not pin transformers** — vLLM ≥0.24 needs
transformers v5, and pinning is what breaks this kit.

`run.sh` preflights the host driver against torch's built CUDA before serving.
That mismatch is the most common pod failure here, and the preflight turns a
wall of engine-core traceback into one line: pick a pod whose CUDA badge is
≥ torch's built CUDA.

## Run

```sh
runpodctl receive <code>
tar xzf lighton_<set>_kit.tar.gz && cd lighton
bash run.sh smoke           # first 8 crops — proves serve + batch + write
bash run.sh full            # the real run
runpodctl send lighton_reads_<set>.tar.gz
```

The model downloads once and stays warm, so `smoke` costs almost nothing before
`full`.

## Decode policy

Small crops make this decoder repeat and degenerate, so `vllm_batch.py`
grants each crop a token budget scaled to its area (a retry entry's own
`decode` settings win), and every request goes out greedy with
`repetition_penalty: 1.15` and `no_repeat_ngram_size: 12`. There is no
quality check here — one attempt per entry, and the pipeline's guards
decide locally what to discard (see "One attempt per entry" above).
Resume is existence-based: an existing read is skipped, a failed crop
writes nothing.

Tunables: `GPUS` (all), `PORT` (8000), `CONCURRENCY` (32), `SMOKE_N` (8).

A40s are the recommended card here as for the other engines, but sizing is
different: LightOn reads only disputed *regions*, not whole pages, so the work is
a small fraction of a full OCR pass and a single GPU is normally enough. The
~3 s/page/GPU budget in the main README's Hardware section applies to the
whole-page engines, not to this one.

## Output

```
reads/<key>.txt — one transcript per crop
```
