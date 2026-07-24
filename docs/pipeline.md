# Pipeline

## Stages (per page, per route)

1. **Render** — redacted source PDF → canonical 1700×2200 PNG (redactions
   applied at render time; every engine sees the identical image).
2. **Layout** — container-YOLO detects containers: column, footnote_block,
   caption, page_number, image, table, heading, blockquote.
3. **Main OCR** — dots.mocr → block bboxes + text.
4. **Supplemental OCR** — route-dependent: Mistral blocks (bbox + text),
   Gemini page XML (no bboxes; generated upstream, consumed as input data),
   or Surya (`surya_line` = line bbox + text, `surya_block` = whole-page
   block bbox + text). The `three_way` route runs TWO supplementals in
   parallel (Mistral blocks + Surya blocks). Every decoded block/line also
   carries `styled`: the engine's native markup (Markdown / XML detail
   tags / HTML) converted to a sanitized HTML vocabulary (`em`, `strong`,
   `sup`) — the end product is HTML, so styling from every engine is
   preserved as HTML from the start.
5. **Reconstruct** — blocks assigned to containers; reading order =
   page_number → body (column, y, x) → footnotes; a bbox outside every
   column orders by (y, x). Image blocks (dots `Picture`, mistral `image`,
   surya `Picture`/`Figure`, gemini `<img>`) keep their reading position but
   contribute no text — the image itself renders in the final. Gemini has
   no bboxes: its blocks order by their `col`/`x`/`y` attributes (not to
   scale; relative order only; a block missing coordinates inherits them
   from its first coordinate-bearing descendant), document order where
   absent; `<img>` tag content (including nested `<p>` captions) is
   in-image text and omitted. `surya_line`: lines are matched to dots
   blocks (kept when a block covers ≥50% of the line, or ≥25% with the
   line's center inside), grouped back into one paragraph per dots block;
   lines matching no block are dropped as bleed-through; lines matching a
   dots Picture block are in-image text and omitted. `surya_block`:
   reconstructs like a block engine (no bleed-through filter — whole-page
   mode has context); blocks mostly inside a dots Picture block are
   in-image text and omitted.
6. **Normalize** — shared equivalence rules applied identically to every
   engine's reconstructed stream (reconstruction happens first and never
   depends on normalization).
7. **Compare + resolve** — single-supplemental routes: diff the two
   reconstructed plaintexts; each dispute maps to its dots block; blocks
   with ≥10 chars of dots content are cropped and sent to LightOn; a
   LightOn read is accepted only if its ending agrees with the dots block
   ending (guards against decoder repetition); disputed tokens are located
   in every read by expanding the surrounding character context until the
   match is unique; majority wins, otherwise the dots reading is kept and
   flagged low-confidence. The `three_way` route instead aligns the three
   engines' block bboxes (dots ∥ Mistral ∥ Surya block) and resolves each
   dispute by direct three-way majority — no crop is ever sent to a
   tiebreaker (avoids LightOn's small-crop decoder hallucination and
   math/LaTeX skew); a three-way split keeps dots and flags
   low-confidence.
8. **Assemble** — dots skeleton + resolved tokens + styling union across
   engines; final HTML with low-confidence marks. Quality is judged by
   comparing the three routes' finals side by side: differences between
   routes are highlighted, and cross-route agreement is the confidence
   signal (there is no reference data in production).

## Artifact contract (v1)

The pipeline writes one JSON document per page × route:

```
data/artifacts/<dataset>/<route>/<page>.json
```

```jsonc
{
  "schema_version": 1,
  "dataset": "sample30",
  "page": "a3d.340.1__p0",
  "route": "mistral",
  "stages": {
    // implemented:
    "render":       {"png": "<path rel. to data/>", "size": [1700, 2200],
                     "n_redaction_rects": 0},
    "layout":       {"engine": "container_yolo", "raw": [], "post": [],
                     "columns": [{"side": "L", "bbox": []}], "stats": {}},
    "main_ocr":     {"engine": "dots", "page_text": "",
                     "blocks": [{"id": 0, "order": 0, "label": "",
                                 "bbox": [], "text": ""}]},
    "supplementals": [
      // one entry per supplemental engine (two on the three_way route):
      // mistral: {"engine", "unit": "block", "missing", "blocks": [...]}
      // gemini:  {"engine", "unit": "page_xml", "refusal", "raw_xml",
      //           "blocks": [{"id", "tag", "attrs", "text"}], "parse_error"}
      // surya:   {"engine", "unit": "line"|"block", "missing", ...}
      {"engine": "mistral", "unit": "block", "blocks": []}
    ],
    "reconstruct":  {
      // per side: order = item ids in reading order; items carry
      // {id, role, band, column, html} (html = styled, sanitized);
      // text = raw plain text joined in that order (image items excluded).
      "main":          {"engine": "dots", "order": [], "items": [], "text": ""},
      // one entry per supplemental. surya_line adds "dropped"
      // (bleed-through line ids) + "in_image" (ids inside a dots Picture
      // block; surya_block adds "in_image" only); gemini adds "omitted"
      // (<img>-tag block ids); surya_line item ids = the dots block each
      // paragraph was grouped under.
      "supplementals": [{"engine": "", "unit": "", "order": [],
                         "items": [], "text": ""}]
    },
    // land in later milestones:
    "normalize":    {"<engine>": {"tokens": [], "rules_applied": []}},
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
