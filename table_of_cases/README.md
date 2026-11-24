# Table of cases: LLM extraction and evaluation

This project turns the scanned Table of Cases of the Twelfth Decennial Digest
(Part 1) into structured records, one per case: case name, court, citations
and later history. (The first prompt also extracted the digest topics; the
current one leaves them out.) The goal is a list of cases and citations
that can be matched against the CourtListener corpus to find what the corpus
is missing.

It contains:

- runners that send one page image to a vision LLM and save the parsed entries,
- a local viewer that shows the scan next to each model's output and builds an
  eval set from the differences between two models,
- a scorer that grades any model's output against that eval set.

## Why an LLM fits this task

- **The scan has no text layer.** All 1,337 pages are images, in three dense
  columns of small type, with entries that run across columns and pages.
- **An LLM returns finished records in one step.** A classic OCR engine returns
  page text; splitting that text into entries and fields then needs a hand-written
  parser, because the bold type that marks the start of an entry is lost. A
  vision LLM reads the page and fills a fixed schema directly.
- **Hallucination carries little risk here.** The extracted citations are not
  added to the corpus. They are looked up in it. A citation the model invents
  or misreads either matches nothing or is caught when the case name does not
  agree, so it cannot introduce a false citation. The output only measures how
  complete the corpus is.
- **The remaining risk is a missed gap.** A misread citation can make a case
  that is in the book look absent from the corpus, or hide a real gap. Matching
  on the citation and the case name together, and reviewing non-matches, keeps
  that contained.

## Use the original-quality scan

Two scans of the volume exist: the original-quality colour scan and a bitonal
(black-and-white) copy at the same pixel size. **Always extract from the
original scan.** The type is very small, and the bitonal copy loses detail in
the digits: an 8 and a 0, or a 3 and a 9, become hard to tell apart even by
eye. Better scan quality gives better extraction. All results below come from
the original scan; the bitonal copy has not been measured.

According to both providers' documentation (checked 2026-11-24), image input
is billed by pixel size or at a fixed rate per image, not by colour depth or
file size, so the bitonal copy saves nothing on cost.

## Data stays on the local machine

Everything under `data/` is git-ignored and must stay that way: the scans, the
model outputs, the page cache, and the eval set. API keys live in `.env`, also
git-ignored (`.env.example` is the template).

Two things to know:

- **Running a runner sends page images to the model provider.** That is the
  only point where page content leaves the machine. The viewer, scorer and
  usage report make no network calls and load nothing from a CDN.
- **The prompt in `toc/prompts/` quotes a few short fragments from the book**
  as examples of the entry format. It is committed with the code.

## Layout

```
toc/
  config.py       paths (everything under data/)
  schema.py       Entry and Page, plus the clean-up rule
  pages.py        page images, the run loop, output files
  run_gemini.py   runner: Gemini API
  run_openai.py   runner: OpenAI API
  batch_openai.py runner: OpenAI Batch API (half price, results within 24 h)
  reprocess.py    rebuild output files from raw responses
  tiers.py        error tiers (Python)
  evalset.py      eval-set file format
  score.py        score outputs against the eval set
  usage.py        tokens, time and cost per engine
  prompts/        table_of_cases_v1.md, _v2.md, _v3.md (--prompt selects)
  viewer/         FastAPI app + static UI (tiers.js holds the same tier rules)
tests/            pytest + one Node test; tier_cases.json is shared by both
data/             git-ignored
  decennial.12th.1.pdf            original scan
  decennial.12th.1.bitonal.pdf    bitonal copy
  outputs/<engine>/page_NNNN.json parsed entries; raw/ keeps responses + usage
  eval/<version>/page_NNNN.json   eval set, one folder per prompt version
  batches/<engine>/               batch request files, manifests, downloaded results
```

An engine folder is named `<model>__<prompt version>__<scan>`, for example
`gpt-6.2-sol__v1__original`.

## Setup

```
uv sync
cp .env.example .env    # then add the keys
```

Put the two PDFs in `data/`.

## Run

Every command runs from the project root.

```
# Extraction: one API call per page. --dry-run prepares everything and calls nothing.
uv run python -m toc.run_gemini --pages 21-40 --dry-run
uv run python -m toc.run_gemini --pages 21-40
uv run python -m toc.run_openai --pages 21-40

# Large runs: OpenAI Batch API. prepare makes no API call; the other three do.
uv run python -m toc.batch_openai prepare --pages 41-1336
uv run python -m toc.batch_openai submit
uv run python -m toc.batch_openai status
uv run python -m toc.batch_openai collect

# Viewer and eval-set builder
uv run uvicorn toc.viewer.app:app --port 8225      # http://127.0.0.1:8225

# Score every engine against the eval set of its prompt version (for v2 on,
# including the history split); report tokens, time and cost.
# --eval-version v1 scores every engine against the v1 eval set instead.
uv run python -m toc.score
uv run python -m toc.usage

# Checks
uv run pytest
node --test tests/tiers.test.js
uv run ruff check toc tests && uv run mypy toc
```

Runner behaviour:

- The whole page goes out as one image at the scan's own pixels.
- Pages that already have output are skipped, so re-running the same command
  retries only the pages that failed. `--overwrite` (synchronous runners
  only) redoes them. Do not overwrite pages an eval set was built from: its
  decisions are tied to the row order of those outputs.
- The prompt defaults to `table_of_cases_v3`; `--prompt` selects another.
- Batch runs: `prepare` writes request files of at most 180 MB each to
  `data/batches/<engine>/` (one batch file may not exceed 200 MB) and skips
  pages that already have output. `collect` keeps every downloaded result
  file, saves the raw text of responses it cannot use, and lists the pages
  that need a retry in the manifest; running `prepare` and `submit` again
  then retries them. Earlier manifests are kept beside the current one.
- Defaults: `gemini-4-hydrogen` with low thinking and ultra-high image detail;
  `gpt-6.2-sol` with low reasoning and `original` image detail. `--model`
  selects another model.
- The full stop that closes a history is removed when the output file is
  written. After changing that rule (`toc.schema.to_page`), run
  `uv run python -m toc.reprocess` to rebuild the output files from the raw
  responses.

## Output format

`data/outputs/<engine>/page_NNNN.json` holds the run settings, token usage and
(shown for prompt v3):

```json
{
  "running_head": "EXAMPLE",
  "topics_extracted": false,
  "entries": [
    {
      "name": "Example Freight, Inc.; Doe v.",
      "court": "XX9 (Yy)",
      "citations": ["100 F3d 200", "2009 WL 1234567"],
      "history": "reh den, cert den Poe v. Example Bank 50 SCt 60",
      "history_items": [
        {
          "action": "reh den, cert den",
          "case_name": "Poe v. Example Bank",
          "citations": ["50 SCt 60"]
        }
      ],
      "topics": null,
      "continued_from_previous_page": false,
      "continues_on_next_page": false
    }
  ]
}
```

- `name` keeps the printed form, including inverted names.
- `history` is always the whole history as one string. `history_items` is the
  same history split into items; it is an empty list for prompt v1, which
  does not split. `topics` is null and `topics_extracted` false from prompt
  v2 on.
- `citations` holds the citations to the decision itself. Citations that follow
  a history phrase (`aff`, `cert den`, …) belong to a different decision and
  stay in `history`.
- The two flags mark an entry cut by a page break, so the pieces can be joined.

## How the eval sets were built

The eval sets cover PDF pages 20–40 (21 pages, 1,235 entries). There is one
per prompt version, because the output format differs.

**v1 eval set** (history as one string, topics included):

1. **Two models from two providers parsed every page** with the same prompt:
   Gemini 4 Hydrogen and GPT-6.2 Sol.
2. **The viewer lined the two outputs up entry by entry** next to the scan.
3. **Entries on which the models agree were accepted automatically.** Agreement
   means the same letters and digits; punctuation and spacing are ignored.
4. **Entries with a real difference went to a review queue.** For each one, a
   reviewer compared both versions with the scan and accepted one, or corrected
   the entry by hand.
5. **Each rejected version received an error tier**, suggested by rule and
   confirmed or changed by the reviewer.

| How an entry entered the v1 eval set (as recorded) | Entries |
|---|---|
| Both models identical | 1,118 |
| Models differ only in punctuation or spacing | 81 |
| Reviewed: one model's version accepted | 35 |
| Reviewed: corrected by hand | 1 |

Only 36 of 1,235 entries (2.9%) carry a human decision. A few of those were
decided before punctuation-only differences were accepted automatically; under
the current rules 29 would be queued. Two independent models and a review of
their disagreements produce an eval set quickly.

**v3 eval set** (history split into items, no topics) reuses the v1 eval set
for the name, court, citations and flags, and adds the history split. Only
split questions are queued: 1,149 entries were identical in both models, 83
differences were settled by the v1 eval set, and 3 carry a human decision
(one of them a real split question).

Rules applied during review:

- A "See …" cross-reference to another entry is not history and is left out.
- One history item is one run of consecutive actions plus the citations that
  follow it; a new item starts only after a citation.

### Error tiers

| Tier | Meaning | Example |
|---|---|---|
| Cosmetic | Only punctuation or spacing differs. Not an error; ignored in scoring. | dash style, a missing bracket |
| Wrong field | The text is right but sits in the wrong field, a list or the history is split in the wrong place, or a page-break flag is wrong. | history placed among the citations |
| OCR error | One or two look-alike characters swapped in a word, nothing else. | `918` for `913` |
| Hallucination | Any other change: a word or citation added, dropped or altered. The most severe. | an added "Inc.", an extra citation |

The look-alike table is `LOOKALIKES` in `toc/tiers.py` and
`toc/viewer/ui/static/tiers.js`.

## Results

Both models, original scan, pages 20–40 (1,235 entries, 1,712 citations, 350
entries with a history). Each prompt is scored against the eval set of its own
version. Prices are list prices for the standard API on 2026-11-24.

### Current prompt: `table_of_cases_v3`

History split into items (`action`, optional `case_name`, `citations`); digest
topics not extracted. **This is the prompt to use.**

| | Gemini 4 Hydrogen | GPT-6.2 Sol |
|---|---|---|
| **Entries right** (of 1,235) | 1,229 (99.51%) | 1,228 (99.43%) |
| Entries missed | 0 | 0 |
| Entries with text in the wrong field or a wrong split | 1 | 0 |
| Entries with an OCR error | 1 | 7 |
| Entries with a hallucination | 4 | 0 |
| **Citations found** (of 1,712) | 1,712 (100%) | 1,712 (100%) |
| Citations read exactly | 1,712 | 1,707 |
| Citations with an OCR misread | 0 | 5 |
| Citations hallucinated | 2 | 0 |
| Case name right | 99.76% | 99.92% |
| Court right | 100% | 100% |
| History text right | 100% | 99.92% |
| History split right (of 350) | 349 | 350 |
| **Cost per page** | $0.078 | $0.061 |
| Cost for the 21 pages | $1.63 | $1.28 |
| Time per page | 33 s | 46 s |
| Tokens per page (input / output) | 4,163 / 9,536 | 10,780 / 3,941 |

What the numbers mean:

- **Both models find every entry and every citation.** Neither skips content,
  which is the main thing a completeness check needs.
- **Overall accuracy is level, about 99.5% of entries.** The difference is in
  the kind of error.
- **GPT-6.2 Sol's errors are all misread characters**, usually a 3 read as an
  8. Five fall in citations.
- **Gemini 4 Hydrogen's errors are mostly changes to the text**: two entries
  gain a citation that is not on the page, and two case names lose or change a
  word.
- **Both models follow the split rule.** Sol divides every history as the eval
  set does; Hydrogen splits one run of actions in two. The split score counts
  the division only, not how the text inside an item was read.
- **GPT-6.2 Sol costs about a fifth less per page; Hydrogen is faster.**
- **Projected cost for the whole volume** (about 1,300 list pages) is roughly
  $80 with GPT-6.2 Sol and $100 with Gemini 4 Hydrogen at the standard rate.
  Batch processing is listed at half price; one page run through the OpenAI
  Batch API returned in about three minutes with the same request accepted.

### First prompt: `table_of_cases_v1`

History as one string; topics included.

| | Gemini 4 Hydrogen | GPT-6.2 Sol |
|---|---|---|
| Entries right (of 1,235) | 1,220 (98.79%) | 1,219 (98.70%) |
| Entries with text in the wrong field | 2 | 1 |
| Entries with an OCR error | 4 | 15 |
| Entries with a hallucination | 9 | 0 |
| Citations read exactly (of 1,712) | 1,712 | 1,708 |
| Citations hallucinated | 3 | 0 |
| Other text filed as a citation | 4 | 0 |
| Topics right | 99.60% | 99.43% |
| Cost per page | $0.124 | $0.080 |
| Time per page | 56 s | 69 s |

### What changed between the prompts

- **Leaving topics out cut cost** by 37% for Hydrogen and 24% for Sol, and
  removed the field that held the largest share of Sol's digit misreads.
- **Giving history citations their own place** stopped Hydrogen filing history
  and topics among the citations (4 cases under v1, none under v3).
- **Stating that a run of actions is one item** made the split unambiguous. An
  intermediate prompt (v2: one item per phrase) had the two models agree on
  only 88 of 95 histories in a five-page trial.

### Limits

- **An error both models make in the same way is invisible.** An entry is
  accepted when the two models agree, so the scores are an upper bound.
- **The sample is 21 consecutive pages from the start of the letter A.** Other
  parts of the volume may behave differently.
- **Punctuation that changes meaning is ignored.** `62, 12` and `62.12` count
  as the same.
- **Each model ran once per prompt, with one group of settings.**
- **The history split is accepted where the two models agree** and was
  reviewed only where they differ, so a split both models get wrong the same
  way is not caught. Where one model misread the history, the other model's
  split was accepted without review (one entry).
- **Blank pages are not filtered out.** PDF pages 1021 and 1239 look blank;
  check their output after a full run.

## Viewer

- Scan on the left (zoomable; switch between the two scans), outputs on the
  right. Two engines share one scroll and matching entries sit on the same row.
- Text that differs between the two outputs is highlighted. "Only real
  differences" hides rows that are identical or differ only in punctuation.
- With exactly two engines shown, the panel is the eval-set builder: click a
  version to accept it, or use the keys.

| Key | Action |
|---|---|
| `1` / `2` | accept the left / right model's version |
| `e` | edit the entry and save the corrected version |
| `u` | undo the choice |
| `n` | next entry to decide |
| `←` `→` | previous / next page |
| `+` `−` `0` | zoom in, zoom out, fit width |
| `g` | go to page |

There is one eval set per prompt version, because the output format differs:
`data/eval/v1/`, `data/eval/v3/`, and so on. The builder is active when both
engines shown come from the same prompt version.

For v2 and later, **only the history split is queued for review** on pages
whose v1 eval set is complete. The v1 entry supplies the name, court,
citations and flags, so a difference the v1 eval set already settles (a
misread digit, an extra citation) is not shown again. The split is taken
from the models that read the history correctly. A row stays open only when
those models split it differently, or when no model read the history
correctly. Picking a version then chooses that model's split, and the edit
form takes the history as one item per line (`action | case name | citation;
citation`).

Choices save as they are made to `data/eval/<version>/page_NNNN.json`: `entries` is the
accepted list for the page, `rows` keeps each decision and the tier per model.
A page is complete when no row is left to decide.

Safeguards and their limits:

- Only a row still to decide reacts to a click or to `1` / `2`. A decided row
  changes through Undo or Edit, and undoing a hand-corrected entry asks first.
- Opening a page for the first time writes its eval file (the automatically
  accepted rows). Opening it again writes only if something changed.
- A save that would drop more than one earlier choice is refused.
- Saved decisions are matched to rows by position. Re-running a model on
  reviewed pages can shift the rows; the page is then blocked only if the row
  count changes. Two tabs editing the same page can overwrite each other.
  Keep a copy of `data/eval/` before either.
