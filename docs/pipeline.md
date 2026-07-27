# Pipeline

## Stages (per page, per route)

1. **Render** — redacted source PDF → canonical 1700×2200 PNG (redactions
   applied at render time; every engine sees the identical image).
2. **Layout** — container-YOLO detects containers: column, footnote_block,
   caption, page_number, image, table, heading, blockquote.
3. **Main OCR** — the route's MAIN engine → region bboxes + text. A
   route is three user-picked models (`<main>+<other>+<resolver>`,
   pipeline/routes.py): the bbox-capable model of the primary pair is
   the main (dots wins when picked; gemini, having no bboxes, can never
   be main); the third slot is LightOn (tiebreaker) or a third model
   (direct three-way vote).
4. **Supplemental OCR** — the remaining picks: dots or Mistral blocks
   (bbox + text), Gemini page XML (no bboxes; generated upstream,
   consumed as input data), or Surya (`surya_line` = line bbox + text,
   `surya_block` = whole-page block bbox + text). A vote route runs two
   supplementals in parallel. Every decoded block/line also
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
   in-image text and omitted. `surya_line` as supplemental: lines are
   matched to the MAIN engine's blocks (kept when a block covers ≥50% of
   the line, or ≥25% with the line's center inside), grouped back into
   one paragraph per main block; lines matching no block are dropped as
   bleed-through; lines matching a main image block are in-image text
   and omitted. Every block engine (dots, mistral, surya block mode)
   reconstructs through ONE path, main or supplemental; a supplemental
   text block mostly inside a main image block is in-image text and
   omitted. A dots Picture / mistral image block whose
   crop is ≥80% near-black is a REDACTION placeholder, not a figure —
   excluded and reported (`redactions`; real figures observed ≤0.74).
   Gemini parallel-reporter star pages (`parallelpagenumber`, ★306) are
   structural markers gemini alone emits: reported (`star_pages`), never
   placed in the content stream.
6. **Normalize** — each reconstructed stream becomes canonical tokens
   (reconstruction happens first and never depends on normalization). A
   token separates rendering from comparison: `display` is the engine's
   text (verbatim, except a plain wrap hyphen is removed when the split
   word is joined), `key` is the comparison form (stage 7 diffs keys
   and nothing else — casing is deliberately preserved, and words and
   symbols are SEPARATE comparison units: `word,` is `word` + `,`, so a
   punctuation difference shows up as exactly that and never breaks a
   word match), `item` + `span` are provenance — the reconstruct item
   and char span the token came from, so a disputed token maps to its
   bbox by lookup, and spans never exceed their item's text. When the
   unit split divides a wrap-joined word, each unit carries the exact
   span of the fragment it came from; only the unit that straddles the
   join keeps the two-bbox `wrap` record — and
   `styling`/`marks` carry emphasis and footnote marks (marks are never
   comparison content; their sequences get their own comparison flow at
   stage 7). All rules live in one ordered registry
   (`pipeline/core/normalize.py`): stream rules transform the token
   stream (footnote-mark extraction in four shapes, NFKC, wrap-hyphen
   joins that never cross a block-role boundary, the word/symbol unit
   split, quote/bullet/dash glyph folds, redaction-square and
   emphasis-residue cleanup, heading markers); decode
   entries document the per-engine format decoding in `core/markup.py`
   (dots/mistral markdown, LightOn math/LaTeX, surya HTML with `<li>`
   bullets, gemini payload repair + star pages). A rule declares its
   layer (`shared` applies to every engine identically; an engine-named
   layer only that engine's format), what it may change, and example
   strings that are executed verbatim as unit tests. The registry hash
   is stamped into every artifact. Rules are decided one at a time: `uv
   run python manage.py report_disagreements --dataset <name>` ranks
   cross-engine disagreement pairs, and the walkthrough's stage-6 card
   shows exactly what every rule changed on a page.
7. **Compare + resolve** — tiebreak routes (lighton in the third
   slot): diff the two reconstructed plaintexts; each dispute maps to
   its main-engine block; blocks with ≥10 chars of main content are
   cropped and sent to LightOn; a LightOn read is accepted only if its
   ending agrees with the main block ending (guards against decoder
   repetition); disputed tokens are located in every read by expanding
   the surrounding character context until the match is unique;
   majority wins, otherwise the main reading is kept and flagged
   low-confidence. A VOTE route (a model in the third slot) instead
   aligns the three engines' regions and resolves each dispute by
   direct three-way majority — no crop is ever sent to a tiebreaker
   (avoids LightOn's small-crop decoder hallucination and math/LaTeX
   skew); a three-way split keeps the main reading and flags
   low-confidence.
8. **Assemble** — main-engine skeleton + resolved tokens + styling
   union across engines; final HTML with low-confidence marks. Quality
   is judged by comparing different routes' finals side by side:
   differences are highlighted, and cross-route agreement is the
   confidence signal (there is no reference data in production).

## Artifact contract (v3)

The pipeline writes one JSON document per page × route:

```
data/artifacts/<dataset>/<route>/<page>.json
```

```jsonc
{
  "schema_version": 3,
  "dataset": "sample30",
  "page": "a3d.340.1__p0",
  "route": "dots+mistral+lighton",
  "tiebreak": "lighton",          // null on vote routes
  "stages": {
    // implemented:
    "render":       {"png": "<path rel. to data/>", "size": [1700, 2200],
                     "n_redaction_rects": 0},
    "layout":       {"engine": "container_yolo", "raw": [], "post": [],
                     "columns": [{"side": "L", "bbox": []}], "stats": {}},
    "main_ocr":     {"engine": "dots", "page_text": "",
                     // image blocks carry "black_frac" (redaction test)
                     "blocks": [{"id": 0, "order": 0, "label": "",
                                 "bbox": [], "text": "", "styled": ""}]},
    "supplementals": [
      // one entry per supplemental engine (two on vote routes):
      // mistral: {"engine", "unit": "block", "missing", "blocks": [...]}
      // gemini:  {"engine", "unit": "page_xml", "refusal", "raw_xml",
      //           "blocks": [{"id", "tag", "attrs", "text"}], "parse_error"}
      // surya:   {"engine", "unit": "line"|"block", "missing", ...}
      {"engine": "mistral", "unit": "block", "blocks": []}
    ],
    "reconstruct":  {
      // per side: order = item ids in reading order; items carry
      // {id, role, band, column, text, html} (text = raw plain text,
      // the normalization input; html = styled, sanitized);
      // text = raw plain text joined in that order (image items excluded).
      // bbox-placed engines also report "no_bbox": ids of blocks that
      // carried no bbox and could not be placed (reported, never lost).
      "main":          {"engine": "dots", "order": [], "items": [], "text": ""},
      // one entry per supplemental. surya_line adds "dropped"
      // (bleed-through line ids) + "in_image" (ids inside a dots Picture
      // block; surya_block adds "in_image" only); gemini adds "omitted"
      // (<img>-tag block ids) + "star_pages" (parallel-reporter markers,
      // reported, never content); surya_line item ids = the dots block
      // each paragraph was grouped under, and each item lists its member
      // "lines" in join order (line -> block provenance). Block engines
      // report "redactions": image-block ids dropped as near-black
      // redaction placeholders.
      "supplementals": [{"engine": "", "unit": "", "order": [],
                         "items": [], "text": ""}]
    },
    "normalize":    {
      // the registry that produced these streams, stamped ("stream"
      // rules transform tokens here; "decode" entries document the
      // per-engine format decoding applied at stages 3-4):
      "registry": {"hash": "1a2b3c4d5e6f",
                   "rules": [{"name": "<rule>", "layer": "shared",
                              "kind": "stream", "applies_to": "key",
                              "description": "", "n_examples": 2}]},
      // per stream: canonical tokens + what every stream rule changed.
      // token = {display, key, item, span} (+ styling/marks/wrap when
      // set; wrap = [item, start, end] of a wrap-joined 2nd fragment);
      // rules follow stream-rule order; changes power the per-rule diff
      // view ({at, before: [token...], after: [token...]}).
      "main":          {"engine": "dots", "tokens": [], "rules": []},
      "supplementals": [{"engine": "", "unit": "", "tokens": [], "rules": []}]
    },
    // land in later milestones:
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
      dots/              dots block reads (JSON)
      gemini/            gemini page XML (generated upstream)
      mistral/           mistral whole-page block reads (JSON)
      surya/line/        surya line reads (surya_line picks)
      surya/block/       surya block reads (surya_block picks)
      lighton_crops/     cached tiebreak crop reads (keyed page + bbox)
  weights/               container-YOLO weights (container_round2.pt)
  artifacts/<dataset>/<route>/   pipeline outputs (contract above)
```
