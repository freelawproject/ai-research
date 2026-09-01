# Block-tagger pipeline — preprocess · infer · postprocess

Runs the published tagger
([freelawproject/caselaw-block-tagger](https://huggingface.co/freelawproject/caselaw-block-tagger))
end-to-end on OCR pipeline output, producing tagged documents the
delivery viewer can display.

```
OCR artifacts ──preprocess.py──▶ canonical docs ──infer.py──▶ raw spans
                                                                 │
        viewer “pipeline samples” ◀── data/samples ◀──postprocess.py
```

## Input

A directory of assembled per-page artifacts from the FLP extraction
pipeline, named `<range>__page_NNN.json`, each containing
`stages.assemble.html` — the page's blocks as minimal HTML with
`data-role` attributes.

**`sample_input/`** — shared separately (gitignored, like all data):
ready-to-run input of 77 pages across five val/test reporter ranges
(`sct.143.0099-0108`, `se2d.911.0482-0494`, `so3d.398.0858-0871`,
`br.670.0513-0528`, `p3d.515.0754-0777`), trimmed to the `assemble`
stage the pipeline reads (the full artifacts also carry the upstream
OCR stages, which are unused here). Place the folder at
`pipeline/sample_input/`; running the three stages over it reproduces
the shipped `data/samples/` docs exactly.

## Stages

1. **`preprocess.py`** (stdlib only) — pages → one canonical doc per
   range: roles mapped to `<p>`/`<blockquote>`, consecutive footnotes
   grouped into a `<footnotes>` band, page numbers dropped,
   `<em>`/`<sup>` kept, whitespace collapsed. Writes
   `work/docs/<range>.json` with a page map (char extents per page).

2. **`infer.py`** (torch + transformers) — windows each doc at block
   boundaries under an 8,000-token budget, runs the tagger, decodes
   BIO predictions to raw character spans. Defaults to the Hugging
   Face model; `--model` accepts a local checkpoint. Writes
   `work/raw/<range>.json`.

3. **`postprocess.py`** — structural confinement (spans never cross
   block markup, edges hold no markup/whitespace — repo-root
   `spannorm.py`), drops any span overlapping a `<footnotes>` band
   (footnote bands are never tagged), optional
   `--union-layout-headings`. Writes the final
   `{range, text, pages, block_spans}` docs to `../data/samples/`,
   where the viewer's **pipeline samples** dataset picks them up.

## Run

With `sample_input/` placed in this folder:

```bash
cd pipeline
uv sync
uv run python preprocess.py --artifacts sample_input
uv run python infer.py
uv run python postprocess.py

cd .. && uv run uvicorn app:app --port 8180
# → http://127.0.0.1:8180 — pick “pipeline samples”
```

For other data, point `--artifacts` at any directory of
`<range>__page_NNN.json` artifacts (optionally `--ranges R [R ...]`).

`sample_input/`, `work/`, and `data/` are gitignored — input data,
intermediates, and final outputs never get committed.
