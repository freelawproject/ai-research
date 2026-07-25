# Case Law Extraction

Production package for extracting clean, reading-ordered text from scanned,
redacted case-law reporter pages, plus a Django viewer for stepping through
every stage of the pipeline — per page, per route, per engine.

## How it works

Every page runs through: render → layout (container-YOLO) → main OCR
(dots.mocr) → supplemental OCR → reading-order reconstruction →
normalization → cross-engine comparison with LightOn tiebreak → final
assembly. Full stage spec and artifact schema:
[docs/pipeline.md](docs/pipeline.md).

Five routes. Four pair dots with one supplemental engine and use LightOnOCR
to tie-break disputes; the fifth resolves by direct three-way majority:

| Route | Supplemental engine(s) | Resolution |
|---|---|---|
| `gemini` | Gemini page XML (generated upstream — bring your own outputs) | LightOn tiebreak |
| `mistral` | Mistral OCR blocks (cached outputs bundled; API runner arrives with the RunPod kits) | LightOn tiebreak |
| `surya_line` | Surya line reads (fine geometry; bleed-through filtered against dots blocks) | LightOn tiebreak |
| `surya_block` | Surya block reads (whole-page context; no line hallucinations) | LightOn tiebreak |
| `three_way` | Mistral blocks + Surya blocks, in parallel with dots | direct three-way bbox-aligned comparison — no tiebreaker model |

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
4. Generate the pipeline artifacts (seconds; reads the bundled engine
   outputs, no models or API keys needed):

   ```
   uv run python -m pipeline.run --dataset sample30 --route all
   ```

5. `docker compose up`, then open http://localhost:8170 and click a route to
   walk the pipeline page by page.

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
reconstruction) and the walkthrough viewer are implemented for all five
routes; artifacts regenerate from the bundled engine outputs in seconds.
Normalization (6), compare/tiebreak (7), and assembly (8) land in upcoming
milestones, followed by the RunPod kits.
