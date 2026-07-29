# Pipeline

## Stages (per page, per route)

1. **Render** — redacted source PDF → canonical 1700×2200 PNG (redactions
   applied at render time; every engine sees the identical image).
2. **Layout** — container-YOLO detects containers: column, footnote_block,
   caption, page_number, image, table, heading, blockquote.
3. **Main OCR** — the route's MAIN engine → region bboxes + text. On
   a page where the main's output is missing or blank, the next bbox
   engine in the combination takes over as the skeleton (priority
   dots > mistral > surya), the blank engine drops to a degraded
   supplemental, and everything the remaining engines disagree on is
   flagged low-confidence (`main_ocr.fallback_from` records the
   handover). A
   route is three user-picked models (pipeline/routes.py) treated as a
   SET — order never matters, so there are exactly 7 unique
   combinations (3 tiebreak pairs + 4 vote trios), each with one
   canonical name and one artifact directory (non-canonical URL
   orderings serve as aliases). The main is the highest-priority bbox
   model in the set — dots > mistral > surya_block (gemini, having no
   bboxes, can never be main); LightOn in the third slot means a
   tiebreak route, a third model means a direct three-way vote. A
   lighton tiebreak needs dots in the combination: the cached tiebreak
   crops are cut from dots blocks, so a non-dots tiebreak route could
   never vote.
4. **Supplemental OCR** — the remaining picks: dots or Mistral blocks
   (bbox + text), Gemini page XML (no bboxes; generated upstream,
   consumed as input data), or Surya (`surya_block` = whole-page block
   bbox + text). A vote
   route runs two supplementals in parallel. Every decoded block also
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
   in-image text and omitted. Every block engine (dots, mistral, surya
   block mode) reconstructs through ONE path, main or supplemental; a
   supplemental text block mostly inside a main image block (≥50%
   cover) is in-image text and omitted. Any block whose crop is
   ≥80% near-black — a placeholder image OR text an engine hallucinated
   over a redacted region — is redaction territory: excluded and
   reported (`redactions`; real figures observed ≤0.74, real text far
   lower).
   Gemini parallel-reporter star pages (`parallelpagenumber`, ★306) are
   structural markers gemini alone emits: reported (`star_pages`), never
   placed in the content stream.
6. **Normalize** — each reconstructed stream becomes canonical tokens
   (reconstruction happens first and never depends on normalization). A
   token separates rendering from comparison: `display` is the engine's
   text (verbatim, except a plain wrap hyphen is removed when the split
   word is joined), `key` is the comparison form (the compare stage diffs keys
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
   comparison content; their sequences get their own comparison flow
   at compare + resolve). All rules live in one ordered registry
   (`pipeline/core/normalize.py`): stream rules transform the token
   stream (footnote-mark extraction in four shapes, NFKC, wrap-hyphen
   joins that never cross a block-role boundary — and when an engine
   wrote the complete word again in the continuation block, the join
   keeps it once — the word/symbol unit
   split, quote/bullet/dash glyph folds, redaction-square and
   emphasis-residue cleanup, heading markers); decode
   entries document the per-engine format decoding in `core/markup.py`
   (dots/mistral markdown, LightOn math/LaTeX, surya HTML with `<li>`
   bullets, gemini payload repair + star pages). Parsed TABLES keep
   their structure in every engine's decode (sanitized
   table/tr/th/td, attributes stripped): cell text stays comparable
   unit by unit, and the table renders as a table. A rule declares its
   layer (`shared` applies to every engine identically; an engine-named
   layer only that engine's format), what it may change, and example
   strings that are executed verbatim as unit tests. The registry hash
   is stamped into every artifact. `uv run python manage.py
   report_disagreements --dataset <name>` ranks cross-engine
   disagreement pairs (the input for new rules), and the walkthrough's
   Normalize card shows exactly what every rule changed on a page.
7. **Compare + resolve** — comparison runs on the canonical unit KEYS
   only (`pipeline/core/compare.py`); every disagreement run is one
   DISPUTE, and a dispute maps to its main-engine block(s) by token
   provenance (a wrap-joined word maps to BOTH its source bboxes) —
   never by heuristic box attribution. Tiebreak routes (lighton in the
   third slot): `page_number`/`heading` blocks are main-authoritative
   (LightOn over-reads short crops); blocks under 10 chars of main
   content are never cropped; otherwise the block crop's CACHED
   LightOn read (`engines/lighton_crops/`, keyed page + exact bbox; a
   cache miss is an honest no-vote) is decoded + normalized like any
   engine stream, accepted only if its last 12 chars agree with the
   block ending as EITHER stream wrote it (decoder repetition/
   truncation guard), and the dispute is located inside it by EXPANDING ANCHORS — n units of
   context each side, widened until the anchor pair is unique.
   Majority of normalized keys wins; anything short of a majority
   keeps the main reading flagged low-confidence. A VOTE route (a
   model in the third slot) diffs the main stream against both
   supplementals, merges the disagreement regions into intervals, and
   resolves each by direct 2-of-3 majority — no crop is ever sent to a
   tiebreaker (avoids LightOn's small-crop decoder hallucination and
   math/LaTeX skew); a three-way split keeps the main reading and
   flags low-confidence. A paired deletion + insertion of
   near-identical text (≥90% similar, ≥5 units) is a REORDER — both
   streams carry the content and disagree only on where it belongs
   (e.g. physical vs semantic footnote order): both disputes resolve
   to the main's placement, not low-confidence. Footnote-mark
   sequences are compared in their
   own flow and reported (marks are never text content); an empty
   supplemental stream (e.g. a gemini refusal) degrades the page —
   reported, main carries it. The resolution parameters are stamped
   into every artifact. Corpus view: `uv run python manage.py
   report_disputes --dataset <name>`.
8. **Assemble** — the final page (`pipeline/core/assemble.py`). The
   main engine's reconstruction is the skeleton: its items in reading
   order, one HTML block element per item, carrying the item's role
   (`data-role`); an image block holds its position as an empty
   `<figure data-bbox>` placeholder the consumer crops from the
   canonical page PNG. The skeleton fills with the main normalized
   stream, every compare + resolve verdict applied: where a
   supplemental's reading won the majority its tokens substitute the
   main tokens (an insertion lands at the dispute's anchor item);
   everywhere else the main tokens stand, and a low-confidence dispute
   renders wrapped in `<mark class="low-confidence">` (high-risk adds
   a class) — the final flags exactly the spans worth a human look. A
   low-confidence dispute whose main side is empty (an engine read
   extra text that did not win) renders a zero-width ∅ marker whose
   title carries the other reading, so it stays visible without
   contributing content.
   The presentation restores the source spacing: the unit split keeps
   `word,` as two comparison units, but a unit whose provenance shows
   it was contiguous with its predecessor renders glued to it —
   punctuation reattaches to its word, free-standing symbols keep
   their space. A structural item (a table) renders by SPLICING the
   resolved tokens back into its own sanitized html at their
   provenance spans — rows and cells stay intact while disputes still
   substitute and mark.
   Styling is a UNION across engines aligned by unit key (display
   only): wherever a stream agrees with the final unit, any emphasis
   it saw joins that unit. Footnote marks ride their tokens and render
   as `<sup>` after the word; gemini parallel-reporter star pages are
   re-inserted at their aligned final position (a marker that arrived
   on a winning substituted token is never doubled). Quality is judged
   route-vs-route — the viewer's compare view puts two finals side by
   side (README); cross-route agreement is the confidence signal
   (there is no reference data in production).

## Artifact contract (v11)

The pipeline writes one JSON document per page × route:

```
data/artifacts/<dataset>/<route>/<page>.json
```

```jsonc
{
  "schema_version": 11,
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
    "main_ocr":     {"engine": "dots",
                     // set when the route's main was missing/blank and
                     // the next bbox engine took over on this page:
                     // "fallback_from": "dots",
                     // every bbox block carries "black_frac" (redaction test)
                     "blocks": [{"id": 0, "order": 0, "label": "",
                                 "bbox": [], "text": "", "styled": ""}]},
    "supplementals": [
      // one entry per supplemental engine (two on vote routes):
      // mistral: {"engine", "unit": "block", "missing", "blocks": [...]}
      // gemini:  {"engine", "unit": "page_xml", "refusal", "raw_xml",
      //           "blocks": [{"id", "tag", "attrs", "text"}], "parse_error"}
      // surya:   {"engine", "unit": "block", "missing", "blocks": [...]}
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
      // one entry per supplemental. Block supplementals report
      // "in_image" (ids of text blocks inside a main image block);
      // gemini adds "omitted" (<img>-tag block ids) + "star_pages"
      // (parallel-reporter markers, reported, never content). Block
      // engines report "redactions": near-black block ids dropped as
      // redaction territory.
      "supplementals": [{"engine": "", "unit": "", "order": [],
                         "items": [], "text": ""}]
    },
    "normalize":    {
      // the registry that produced these streams, stamped ("stream"
      // rules transform tokens here; "decode" entries document the
      // per-engine format decoding applied when outputs are decoded):
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
    "compare": {
      // the resolution parameters this stage ran under:
      "params": {"gate_min_chars": 10, "tail_agree_chars": 12,
                 "anchor_start": 2, "skip_roles": ["heading", "page_number"],
                 "high_risk_chars": 5, "reorder_sim": 0.9,
                 "reorder_min_units": 5},
      // stream legend: reads/spans/verdicts key on these labels
      "streams": [{"label": "main", "engine": "dots"},
                  {"label": "supp1", "engine": "mistral", "unit": "block"}],
      "degraded": [],                 // labels of empty streams (refusals)
      "disputes": [{
        "id": 0,
        "main_span": [12, 13],        // token indices in the main stream
        "spans": {"supp1": [12, 13]}, // aligned token span per supplemental
        "blocks": [3],                // main items (provenance; wraps add both)
        "roles": ["content"],
        "reads":    {"main": "brown", "supp1": "brawn", "lighton": "brawn"},
        "displays": {"main": "brown", "supp1": "brawn", "lighton": "brawn"},
        "verdict": "supp1",           // the winning stream label
        "resolution": "majority",     // majority | reorder | authoritative | fallback
        "reason": null,               // fallback/authoritative cause:
                                      // under-gate | role-authoritative |
                                      // no-cached-read | read-rejected |
                                      // not-located | three-way-split | no-vote
        "low_confidence": false,
        "high_risk": false,          // low-confidence AND disputed text
                                      // > high_risk_chars on some side
        // tiebreak routes only — the LightOn attempt trace
        // (accepted_by = which stream's block ending the read matched;
        // a rejected read records the three "tails" instead):
        "tiebreak": {"bboxes": [[318, 191, 856, 336]], "cached": true,
                     "accepted": true, "accepted_by": "main",
                     "located": true,
                     "read": "…", "keys": ["brawn"], "display": "brawn"}
      }],
      // footnote-mark sequences, compared in their own flow:
      "marks": [{"label": "supp1", "main": ["4"], "supp": ["4"],
                 "agree": true, "diffs": []}],
      "metrics": {"main_units": 787, "disputed_units": 14, "n_disputes": 6,
                  "by_resolution": {"majority": 3, "fallback": 3},
                  "by_reason": {"not-located": 2, "read-rejected": 1},
                  "n_low_confidence": 3, "n_high_risk": 1,
                  // tiebreak routes only:
                  "tiebreak": {"attempted": 6, "cached": 6,
                               "accepted": 5, "voted": 3}}
    },
    "assemble": {
      // the final page: the main reconstruction's items in reading
      // order (<p>/<h2>/<blockquote> per role, data-role/data-item
      // attributes), filled with the resolved token stream — winning
      // supplemental readings substituted, low-confidence disputes
      // wrapped in <mark class="low-confidence"> (+" high-risk"; an
      // empty main side renders a zero-width ∅ marker instead),
      // styling unioned across engines as <em>/<strong>/<u>/<sub>,
      // footnote marks as <sup>, star pages as
      // <span class="star-page">, image blocks as empty
      // <figure data-bbox> placeholders (crop them from render.png):
      "html": "<p data-item=\"0\" data-role=\"content\">…</p>",
      "text": "…",                    // plain reading text, one line per item
      "low_confidence": [3, 5],       // dispute ids the html marks
      "metrics": {"units": 787, "substitutions": 2, "substituted_units": 3,
                  "n_low_confidence": 2, "n_high_risk": 1,
                  "styling_unioned": 4, "star_pages": 0}
    }
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
    redacted/            source volume trees (PDFs + redaction rects), OR
                         flat: one single-page redacted PDF per page
                         (sampled sets; the PDF stem is the page id)
    page_png/            canonical rendered pages (what every engine saw)
    engines/
      container_yolo/    layout detections per page (JSON)
      dots/              dots block reads (JSON)
      gemini/            gemini page XML (generated upstream)
      mistral/           mistral whole-page block reads (JSON; a page may
                         hold the whole response object {markdown, blocks})
      surya/block/       surya block reads (surya_block picks)
      lighton_crops/     cached tiebreak crop reads (keyed page + bbox)
  weights/               container-YOLO weights (container_round2.pt)
  artifacts/<dataset>/<route>/   pipeline outputs (contract above)
  exports/               LightOn crop bundles (manage.py export_crops)
```

A dataset missing its LightOn crop cache runs the tiebreak combinations
with every crop as an honest no-vote. To fill the cache:
`manage.py export_crops --dataset <name>` builds the tiebreak
combinations' artifacts, collects every disputed region without a
cached read, and writes the LightOn pod kit's input bundle
(crops/<key>.png + manifest.jsonl; key = `<page>_<x0>_<y0>_<x1>_<y1>`)
to `data/exports/lighton_<name>/`; after the pod run,
`manage.py ingest_reads --dataset <name> <reads tarball>` lands the
reads in the cache, and re-running the combinations picks up the votes.
