# Case Law Extraction

Production package for extracting clean, reading-ordered text from scanned,
redacted case-law reporter pages, plus a Django viewer for stepping through
every stage of the pipeline — per page, per route, per engine.

## How it works

Every page runs through: render → layout (container-YOLO) → main OCR →
supplemental OCR → reading-order reconstruction → normalization →
cross-engine comparison and resolution → final assembly. Full stage
spec and artifact schema: [docs/pipeline.md](docs/pipeline.md).

A route is any THREE models, picked in the viewer's dropdowns (or on
the CLI as `m1+m2+m3`):

- **Slots 1 + 2 — two OCR models**, from: `dots`, `mistral`, `gemini`
  (page XML generated upstream — bring your own outputs),
  `surya_block`.
- **Slot 3 — the resolver**: `lighton` re-reads disputed crops as a
  tiebreaker, or any third model joins a direct three-way vote (no
  tiebreaker model).

A combination is a SET — order never matters. `dots+gemini+mistral`
IS `dots+mistral+gemini`; every ordering canonicalizes to one route
name and one artifact directory. The MAIN engine (reconstruction
skeleton + fallback when all reads disagree) is the highest-priority
bbox model in the set: dots > mistral > surya_block (gemini has no
bboxes and can never be main). On a page where the main's output is
missing or blank, the next bbox engine in that order takes over and
the remaining engines' differences are flagged low-confidence. A lighton tiebreak additionally needs
dots in the combination — the cached tiebreak crops are cut from dots
blocks, so a non-dots tiebreak route could never vote. That makes
exactly 7 unique combinations: 3 tiebreak pairs (dots + one other
model + lighton) + 4 vote trios.

All 7 combinations are first-class: `--route all` builds every one a
dataset presents, and the home page's route stats table reports compare
+ resolve outcomes for each (aggregated from whatever is on disk). The
legacy preset names (`gemini`, `mistral`, `surya_block`, `three_way`)
still resolve as aliases in URLs and on the CLI.

**Which combinations a dataset presents** is a separate question from
which ones exist. A tiebreak combination reads cached LightOn crops, so
a dataset whose crop cache is incomplete would show its tiebreak routes
losing every uncovered dispute to an honest no-vote — next to a vote
trio holding all its inputs, that reads as a worse combination rather
than an unfinished one. Such a dataset is listed in
`VOTE_ONLY_DATASETS` (pipeline/core/config.py) and presents its 4 vote
trios only: they are absent from the stats table and every picker, and
requesting one by URL is a 404. Withholding is opt-in, so a dataset
nobody has ruled on presents all 7, and it is a reporting rule only —
artifacts already on disk are neither deleted nor rebuilt.

## Quickstart

1. Clone this branch.
2. Unzip the data bundle (distributed separately) so it sits at the repo
   root as `./data/`:

   ```
   data/
     datasets/<name>/     page set + cached engine outputs (sample30, …)
     weights/             container-YOLO weights
     artifacts/           pipeline outputs (regenerate any time, step 4)
   ```

   A dataset's `redacted/` sources are either volume trees or flat
   per-page redacted PDFs (sampled sets — the PDF stem is the page id).
   The full layout is documented in [docs/pipeline.md](docs/pipeline.md).
3. Validate the layout: `uv run python manage.py check_data`
   (it prints exactly what's missing if the folder landed in the wrong spot).
4. `docker compose up`, then open http://localhost:8170, pick three
   models, and walk the pipeline page by page. Artifacts materialize on
   first visit (seconds; reads the bundled engine outputs, no models or
   API keys needed). To pre-generate every combination the dataset
   presents instead:

   ```
   uv run python -m pipeline.run --dataset sample30 --route all
   ```

Without Docker: `uv sync && uv run python manage.py runserver 8170`.

## The LightOn tiebreaker on new data

The tiebreak combinations read CACHED crop transcriptions
(`engines/lighton_crops/`); a dataset without that cache still runs —
every crop is an honest no-vote. To fill the cache:

1. `uv run python manage.py export_crops --dataset <name>` — builds the
   tiebreak combinations' artifacts, collects every disputed region
   without a cached read, and writes the LightOn pod kit's input bundle
   (`crops/` + `manifest.jsonl`) to `data/exports/lighton_<name>/`.
2. Run the LightOn kit on that bundle: stage it as
   `runpod/data/sets/<set>/`, `bash runpod/pack.sh lighton <set>`,
   ship the tarball to a pod and `bash run.sh full` (see
   `runpod/kits/lighton/README.md`). A bounded bundle can skip the pod
   entirely — `python runpod/kits/lighton/local_batch.py <bundle>` runs
   the same contract on this machine.
3. `uv run python manage.py ingest_reads --dataset <name>
   lighton_reads_<set>.tar.gz` — lands the reads in the crop cache.
4. `uv run python -m pipeline.run --dataset <name> --route all` —
   rebuild; the tiebreak now votes.

A read the compare stage discards (implausible length, or an ending that
matches neither engine) buys the crop **one retry**: the dispute reports
`retry-pending`, and the next `export_crops` puts that crop back in the
bundle under a `__retry` key carrying the tighter decode settings the
retry runs under. Repeat steps 2–4 to collect it. A retry that fails too
is final — the dispute keeps the main reading, flagged low-confidence.
Every crop is therefore read at most twice, whatever the run count.

## Producing engine outputs on new pages (`runpod/`)

The pipeline CONSUMES cached engine outputs; `runpod/` is what produces
them. One self-contained kit per engine, each a flat directory the pod
unpacks and runs — no imports from this package, so a kit never needs the
pipeline installed beside it:

| Kit | What it runs |
|---|---|
| `runpod/kits/container_yolo/` | the layout detector (weights published separately) |
| `runpod/kits/dots/` | dots.mocr via vLLM (or native transformers) |
| `runpod/kits/surya/` | Surya OCR 2 via vLLM |
| `runpod/kits/lighton/` | LightOn crop reads — pod (vLLM) or local CPU |
| `runpod/mistral/` | Mistral OCR API batches (no pod: it bills per page) |

The flow is the same for every kit. Define a SET under
`runpod/data/sets/<set>/` (gitignored) — usually just a `set.conf` naming
the dataset whose rendered pages to use, plus an optional page list —
then `bash runpod/pack.sh <engine> <set>` builds
`runpod/dist/<engine>_<set>_kit.tar.gz`. On the pod, `GPUS=n bash run.sh`
runs one worker per card over disjoint pages and packages the output
once. A kit takes IMAGES, never PDFs: the canonical 1700×2200 render is
the pipeline's own, so bboxes come back in the space the pipeline reads.
Per-engine detail, pod images, and the measured throughput numbers are in
`runpod/README.md`.

## Developing the UI

Tailwind CSS is committed pre-built; rebuild it after template/JS changes
with `docker compose --profile dev up tailwind` (watch mode) or the one-shot
command in `viewer/static_src/tailwind.config.js`. Alpine.js is the CSP
build, vendored at `viewer/static/viewer/js/vendor/` — components live in
`viewer/static/viewer/js/components/` and MUST load before Alpine.

## Repository map

| Path | What |
|---|---|
| `pipeline/` | the extraction pipeline (pure Python, no Django dependency) |
| `viewer/` | Django app for walking pipeline artifacts |
| `extraction/` | Django project settings (runs without a database) |
| `runpod/` | pod kits that PRODUCE the engine outputs (see below) |
| `docs/` | pipeline spec + artifact schema |
| `docker/` | image + entrypoint for the compose stack |
| `scripts/pack_data.sh` | builds the shareable data zip |

There is no database: the JSON artifacts under `data/` are the source of
truth, and the viewer indexes them in memory. Production persistence is
intentionally out of scope — design it from the artifact schema.

## The viewer

- **Walkthrough** — one page, one combination, every stage: overlays
  on the render, decoded outputs, per-rule normalization diffs,
  dispute cards with the tiebreak trace, and the assembled final.
- **Home** — pick a sample set; its per-combination stats table
  (cached on disk) links into review pages for reorders, rejected,
  low-confidence, and high-risk disputes, filterable by combination.
- **Route compare** (`/compare/<dataset>/<page>/`) — two combinations'
  finals side by side against the page image; plain-text diffs,
  styling diffs, and low-confidence marks each toggle as its own
  highlight layer. Cross-route agreement is the quality signal — there
  is no reference data in production.
- `manage.py report_disagreements` / `report_disputes` — corpus views.
