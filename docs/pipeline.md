# Pipeline

## Stages (per page, per route)

1. **Render** — redacted source PDF → canonical 1700×2200 PNG (redactions
   applied at render time; every engine sees the identical image).
2. **Layout** — container-YOLO detects containers: column, footnote_block,
   caption, page_number, image, table, heading, blockquote.
3. **Main OCR** — dots.mocr → block bboxes + text.
4. **Supplemental OCR** — route-dependent: Mistral blocks (bbox + text),
   Gemini page XML (no bboxes; generated upstream, consumed as input data),
   or Surya lines (bbox + text).
5. **Normalize** — per-engine format decode (markdown/XML/HTML → text +
   styling + marks), then shared equivalence rules applied identically to
   every engine's stream.
6. **Reconstruct** — blocks assigned to containers; reading order =
   page_number → body (column, y, x) → footnotes; a bbox outside every column
   orders by (y, x). Surya lines not contained in any dots block are dropped
   as bleed-through hallucinations.
7. **Compare + tiebreak** — diff the two reconstructed plaintexts; each
   dispute maps to its dots block; blocks with ≥10 chars of dots content are
   cropped and sent to LightOn; a LightOn read is accepted only if its ending
   agrees with the dots block ending (guards against decoder repetition);
   disputed tokens are located in every read by expanding the surrounding
   character context until the match is unique; majority wins, otherwise the
   dots reading is kept and flagged low-confidence.
8. **Assemble** — dots skeleton + resolved tokens + styling union across
   engines; final HTML with low-confidence marks; metrics vs golden where a
   golden reference exists.

## Artifact contract (v1)

The pipeline writes one JSON document per page × route:

```
data/artifacts/<dataset>/<route>/<page>.json
```

```jsonc
{
  "schema_version": 1,
  "pipeline_version": "<hash>",       // stamps code + normalization registry
  "dataset": "golden30",
  "page": "a3d.340.1__p0",
  "route": "mistral",
  "stages": {
    "render":       {"png": "<path>", "size": [1700, 2200]},
    "layout":       {"containers": [{"label": "", "bbox": [], "confidence": 0}]},
    "main_ocr":     {"engine": "dots", "blocks": [{"id": 0, "bbox": [], "text": ""}]},
    "supplemental": {"engine": "", "unit": "block|page_xml|line", "items": []},
    "normalize":    {"<engine>": {"tokens": [], "rules_applied": []}},
    "reconstruct":  {"<engine>": {"order": [], "dropped_lines": []}},
    "compare":      {"disputes": [{"block": 0, "reads": {}, "verdict": ""}]},
    "assemble":     {"final_html": "<path>", "low_confidence": [], "metrics": {}}
  }
}
```

The viewer renders these documents and holds no state of its own. Production
persistence (database, object storage) is intentionally out of scope for this
package — design it from this schema.

## Data layout

```
data/
  datasets/<name>/
    redacted/            source volume trees (PDFs + redaction rects)
    page_png/            canonical rendered pages (what every engine saw)
    golden/              hand-reviewed reference pages (golden30 only)
    engines/
      container_yolo/    layout detections per page (JSON)
      dots/              main-engine block reads (JSON)
      gemini/            supplemental page XML (generated upstream)
      mistral/           supplemental whole-page block reads (JSON)
      surya/line/        supplemental line reads (JSON)
      surya/block/       surya block reads (reference)
      lighton_crops/     cached tiebreak crop reads (keyed page + bbox)
  weights/               container-YOLO weights (container_round2.pt)
  artifacts/<dataset>/<route>/   pipeline outputs (contract above)
```
