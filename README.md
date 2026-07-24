# Case Law Extraction

Production package for extracting clean, reading-ordered text from scanned,
redacted case-law reporter pages, plus a Django viewer for stepping through
every stage of the pipeline — per page, per route, per engine.

## How it works

Every page runs through: render → layout (container-YOLO) → main OCR
(dots.mocr) → supplemental OCR → normalization → reading-order reconstruction
→ cross-engine comparison with LightOn tiebreak → final assembly. Full stage
spec and artifact schema: [docs/pipeline.md](docs/pipeline.md).

Three routes, differing only in the supplemental engine:

| Route | Supplemental engine | Provided by |
|---|---|---|
| `gemini` | Gemini page XML | generated upstream — bring your own outputs |
| `mistral` | Mistral OCR blocks | API runner in this package |
| `surya` | Surya line reads | RunPod kit in this package |

## Quickstart

1. Clone this branch.
2. Unzip the data bundle (distributed separately) so it sits at the repo
   root as `./data/`:

   ```
   data/
     datasets/golden30/   30-page hand-reviewed reference set + engine outputs
     weights/             container-YOLO weights
   ```

   The full layout is documented in [docs/pipeline.md](docs/pipeline.md).
3. `docker compose up`, then open http://localhost:8170.

Without Docker: `uv sync && uv run python manage.py runserver 8170`.

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

Scaffold + data staging. Pipeline stages and the walkthrough UI land next.
