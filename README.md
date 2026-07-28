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
bboxes and can never be main). A lighton tiebreak additionally needs
dots in the combination — the cached tiebreak crops are cut from dots
blocks, so a non-dots tiebreak route could never vote. That makes
exactly 7 unique combinations: 3 tiebreak pairs (dots + one other
model + lighton) + 4 vote trios.

All 7 combinations are first-class: `--route all` builds every one,
and the home page's route stats table reports compare + resolve
outcomes for each (aggregated from whatever is on disk). The legacy preset names
(`gemini`, `mistral`, `surya_block`, `three_way`) still resolve as
aliases in URLs and on the CLI.

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
   API keys needed). To pre-generate all 7 combinations instead:

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

Implemented end to end: render, layout, main + supplemental OCR,
reading-order reconstruction, normalization (canonical tokens with
provenance, styling, and footnote marks; a versioned rule registry
whose example strings run as unit tests), and compare + resolve
(disputes on the normalized unit streams; cached-crop LightOn tiebreak
with ending-agreement guard and expanding-anchor localization; direct
three-way vote). The walkthrough shows every stage per page, the home
page aggregates per-combination stats with linked review pages, and
`manage.py report_disagreements` / `report_disputes` print the corpus
views. Assembly (the final HTML) lands next, followed by the RunPod
kits.
