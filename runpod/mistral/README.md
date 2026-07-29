# Mistral OCR runner (local)

Mistral is a hosted API, so it has no pod kit — it runs from here against the
same staged one-page PDFs the pod kits consume and writes the same per-page
JSON.

## Setup

```sh
cp .env.example .env        # add MISTRAL_API_KEY
uv pip install mistralai pymupdf pillow
```

## Two modes

**realtime** — synchronous `/v1/ocr` per page across a thread pool. Rate-limited
and pricier per page, so it exists to prove the code works before committing a
set:

```sh
python run_mistral.py --set newvols --mode realtime --limit 5
```

**batch** — a batch job per chunk. Each page is uploaded once as an `ocr` file
and referenced by id, so the manifest stays small at any set size:

```sh
python run_mistral.py --set newvols --mode batch
```

Run realtime first, then batch: the five smoke pages already have outputs and
are skipped, so batch picks up the rest.

## Chunking and resume

Work is chunked (`--chunk`, default 500) rather than done in one pass — a full
volume set is thousands of rendered PNGs, and holding all of them in memory to
hand to one batch job is slow to start and easy to lose. Each chunk writes
before the next begins.

Pages that error are **not** written, so re-running the same command retries
exactly the failures and skips everything that succeeded.

## Flags

| | |
|---|---|
| `--set` | a set under `data/sets/` |
| `--mode` | `realtime` \| `batch` |
| `--limit N` | only the first N pages still to do |
| `--chunk N` | pages per batch job / realtime wave (500) |
| `--concurrency N` | realtime threads (8) |
| `--out-dir` | default `data/sets/<set>/out/mistral` |

## Output

```
out/<stem>.json = {"markdown": "...", "blocks": [{type,bbox,text}]}
```

Bboxes in 1700×2200 space, matching the canonical render — the pages go to the
API as rendered PNGs, not as the source PDFs, so Mistral's coordinates land in
the same space as every other engine's.

`include_blocks=True` and `table_format="html"` are always set.

## Contents

- `run_mistral.py` — CLI: stage selection, rendering, chunking, resume
- `mistral_ocr.py` — API layer: `run_ocr_realtime`, `run_ocr_batch`, `parse`
