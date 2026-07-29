# Alignment: what every engine read of the same region

A second surface over the same engine outputs, for sets where the
route pipeline's assumptions do not hold. It answers one question per
region — *what did the engines read here, and what do they agree on?*

Where the route pipeline picks a main engine, reconstructs against the
container model's columns, normalizes, compares and resolves a winner,
this one does four things:

1. **Align** every engine's regions onto the same pieces of the page,
   by geometry alone.
2. **Order** those regions from their own coordinates.
3. **Resolve** each region to the read the engines agree on.
4. **Present** the final page, and each engine's read behind it.

The container model is loaded and drawn over the page image, and that
is all it does here.

## Why

The container detector is trained on redacted pages. On unredacted
ones it collapses: over the 3,541-page `newvols` set it emits 3,319
whole-page `image` boxes and finds **zero columns on 89.3% of pages**.

That is not a cosmetic failure. The route pipeline places text by
container: with no columns, `layout.assign` returns no side for every
block and `reading_order` degrades to a whole-page (y, x) sweep, which
**interleaves the two columns** of a reporter page. Worse, it is
silent — every engine is placed through the same broken container set,
so all three scramble identically and the compare stage reports a
handful of disputes on a page whose text is unreadable.

Measured against dots' own reading order over 897 unredacted pages:

| ordering | pairwise inversion | pages ordered identically |
|---|---|---|
| container-driven (y, x) sweep | 20.37% | 0.9% |
| geometry (`pipeline/core/order.py`) | **0.47%** | **77.4%** |

## Aligning

`pipeline/core/align.py`. Two regions from **different** engines link
when their intersection covers at least `OVERLAP` (0.5) of the
**smaller** one. Containment, not IoU: a small box inside a big one is
obviously the same content but scores badly on IoU. A group is a
connected component of those links, so one engine's three boxes and
another's one box resolve into a single comparable region in one pass.

Text never decides what merges. Two guards keep that honest:

- **Page-scale regions** (≥ `MAX_AREA`, half the page) are held out of
  the link graph. A whole-page picture box overlaps everything and
  would chain the page into one useless group — and unredacted pages
  are full of them.
- **`alignment_iou`** is the worst pairwise IoU of the engines' merged
  boxes. Linking is deliberately permissive; afterwards the merged
  boxes should still occupy nearly the same rectangle. Below 0.3 the
  group is flagged *weak* in the UI, because a text difference it
  shows may be an alignment artefact rather than an OCR disagreement.

Each engine's members of a group merge into one box, with their text
concatenated in reading order.

## Ordering

`pipeline/core/order.py`. A page reads as three bands: the running
heads above the text block, the body, and anything below its foot.
Head and foot are read across, left to right within a line band —
the two running heads sit level, and a pixel of y noise must not
decide which comes first.

The body splits into columns at a boundary inferred from the regions'
**left edges**. A two-column reporter page puts every x0 into one of
two tight clusters hundreds of pixels apart; that survives what a
whitespace hunt does not, since the real gutter is only ~20px wide and
the centered running head straddles it. The boundary is the right
cluster's first edge, not the midpoint of the gap — the left column's
text runs right up to the gutter.

A region straddling the boundary is full-width: it separates the
columns above it from the columns below, and ordering restarts
underneath. Pages with fewer than four body regions, or with no
left-edge gap of at least 200px, are read top to bottom as one column.

Known limit: footnotes. Without containers there is no reliable signal
that a block at the bottom of the left column is a footnote, so it
reads in column order rather than being pulled into a footnote band.
This is the bulk of the residual 0.47%.

## Resolving

`pipeline/core/consensus.py`. Two levels, because most regions never
need the second one.

Most regions resolve **whole**: all three engines return byte-identical
text, or two of three do. The winner's HTML carries through, styling
and all. Over `newvols`: 56.3% unanimous, 24.7% majority.

The remaining **18.5%** differ everywhere — almost always by a
character or two in a long paragraph, with each engine erring in a
different place. Handing those to one engine would quietly give away
**29.3% of the page text**, so they are voted **word by word**: the
other engines are aligned against a base read
(`PRIORITY = dots > mistral > surya`, the same ranking the route
pipeline uses), each position takes the reading a majority share, and
runs the others agree on are inserted or dropped accordingly. A
position with no majority keeps the base's word and renders as
`<mark class="low-confidence">`.

That vote resolves **98.6%** of the text in those regions. What is left
— **0.402% of the whole corpus's words** — is marked rather than
silently chosen.

Word voting cannot carry styling, so a voted region renders as marked
plain text and is badged `voted word by word`. That is deliberate: a
region that resolved whole and one that was assembled from three
disagreeing reads should not look alike.

Each group carries `consensus`:

| field | what |
|---|---|
| `agreement` | `unanimous` / `majority` / `voted` / `single` |
| `source` | the engine whose read is the base or the winner |
| `agreeing` | engines that matched the winner (empty when voted) |
| `html` | the final read, with marks where the vote failed |
| `text` | the same, plain |
| `n_low_confidence` | positions with no majority |

## Running it

```
uv run python -m pipeline.align_run --dataset newvols
uv run python -m pipeline.align_run --dataset newvols --engines dots,surya
```

~13s for 3,541 pages — no rendering, no normalization, no comparison.
Artifacts land at `align_data/artifacts/<dataset>/align/<page>.json` and
also materialize lazily on the first visit to a page.

A page builds as long as **one** engine read it: an engine that
dropped a page loses its column, not the page.

## Artifact

`schema_version: 2`, `kind: "align"`.

| field | what |
|---|---|
| `engines` | engines with output for this page |
| `missing_engines` | engines with none |
| `params` | `overlap`, `max_area` the groups were built with |
| `render` | page PNG path + canonical size |
| `containers` | container-YOLO `raw`/`post`/`columns`/`stats` — overlay only |
| `column_boundary` | inferred x, or `null` for a single column |
| `groups` | the aligned regions, in reading order |
| `metrics` | per-page totals (below) |

Each group: `id` (its reading-order position), `bbox` (union across
every engine present), `band`, `column`, `present`, `page_scale`,
`alignment_iou`, `consensus` (above), and `engines[name]` =
`{n_boxes, ids, bbox, labels, text, html}`.

`metrics`: `n_groups`, `n_all_engines`, `n_page_scale`, `by_pattern`
(which engine sets found a region), `by_agreement` (how each region
resolved), `n_low_confidence`, `boxes_per_engine`, `n_weak_alignment`,
`median_alignment_iou`.

## Viewing

`/align/` picks a set and reports it corpus-wide;
`/align/<dataset>/<page>/` is the page.

The page image carries toggleable layers: the aligned groups, weak
alignments, each engine's merged boxes, the inferred column split, and
container-YOLO (raw and post-processed). Every box's tooltip names its
region number and the engines in it, which is what ties the image to
the region list.

The right side switches between **by region** — each region's final
read, then every engine's, with a badge saying how it resolved — and
**read through**, the page as continuous text. Read-through defaults
to **Final (majority)** and can drop to any single engine.

Two signals, at two scales: **amber** marks a REGION that was voted
word by word — a rule down the paragraph's left edge in read-through,
a `voted word by word` badge on its card. **Rose** marks a WORD the
vote could not settle. A single engine's read shows neither; there is
no agreement to report.

## Datasets without a redacted tree

Unredacted sets have no per-page redacted PDF and no redaction rects,
so `pipeline/core/pages.py` grew a third layout: **prerendered**. The
set ships its page renders under `page_png/` and the whole-volume PDFs
under `source/`; the page id is the render's stem,
`<volume>__page_<n>` with n 1-based.

Align sets live under their own root, `align_data/` — same
`datasets/` + `artifacts/` shape as `data/`, distributed as its own
bundle (`scripts/pack_align_data.sh`). Each surface lists datasets by
scanning its root, so the split is the gate: an align set can never
surface in the route pickers, and a route set never appears under
`/align/`.

```
align_data/datasets/<name>/
  page_png/<volume>__page_0001.png    the render every engine saw
  source/<volume>.pdf                 provenance; never reopened
  engines/{container_yolo,dots,mistral}/<page>.json
  engines/surya/block/<page>.json
```

Discovery reads `page_png/`, so the render cache is complete by
construction and nothing has to rasterize at view time.
`manage.py check_data` validates both roots; `source/` is optional
there (provenance only — a bundle may omit it).
