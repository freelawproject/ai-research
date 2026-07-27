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

- **Slots 1 + 2 — the primary pair**, from: `dots`, `mistral`,
  `gemini` (page XML generated upstream — bring your own outputs),
  `surya_block`, `surya_line`. At least one must have bbox
  capabilities (every model except gemini); the bbox-capable pick is
  the MAIN engine — the reconstruction skeleton and the fallback when
  all reads disagree. When dots is one of the two, dots is the main.
- **Slot 3 — the resolver**: `lighton` re-reads disputed crops as a
  tiebreaker, or any third model joins a direct three-way vote (no
  tiebreaker model).

Canonical presets (`--route all`): `dots+gemini+lighton`,
`dots+mistral+lighton`, `dots+surya_line+lighton`,
`dots+surya_block+lighton`, and `dots+mistral+surya_block` (the
three-way vote).

## Quickstart

1. Clone this branch.
2. Unzip the data bundle (distributed separately) so it sits at the repo
   root as `./data/`:

   ```
   data/
     datasets/sample30/   30-page sample set + cached engine outputs
     weights/             container-YOLO weights
     artifacts/           pipeline outputs (regenerate any time, step 4)
   ```

   The full layout is documented in [docs/pipeline.md](docs/pipeline.md).
3. Validate the layout: `uv run python manage.py check_data`
   (it prints exactly what's missing if the folder landed in the wrong spot).
4. `docker compose up`, then open http://localhost:8170, pick three
   models, and walk the pipeline page by page. Artifacts materialize on
   first visit (seconds; reads the bundled engine outputs, no models or
   API keys needed). To pre-generate the canonical presets instead:

   ```
   uv run python -m pipeline.run --dataset sample30 --route all
   ```

Without Docker: `uv sync && uv run python manage.py runserver 8170`.

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
| `docs/` | pipeline spec + artifact schema |
| `docker/` | image + entrypoint for the compose stack |
| `scripts/pack_data.sh` | builds the shareable data zip |

There is no database: the JSON artifacts under `data/` are the source of
truth, and the viewer indexes them in memory. Production persistence is
intentionally out of scope — design it from the artifact schema.

## Status

Stages 1–5 (render, layout, main OCR, supplemental OCR, reading-order
reconstruction) and the walkthrough viewer are implemented for every
model combination; artifacts materialize from the bundled engine
outputs in seconds (first viewer visit, or `pipeline.run`).
Normalization (6) is implemented: canonical tokens with provenance,
styling, and footnote marks, plus a versioned rule registry (14 stream
rules + the per-engine decode rules; casing deliberately preserved;
words and symbols compared as separate units).
Rules were decided one at a time against cross-engine disagreement
reports (`manage.py report_disagreements`); each shows a per-rule diff
in the walkthrough, and its example strings run as unit tests. Compare/tiebreak (7) and assembly (8) land in upcoming
milestones, followed by the RunPod kits.
