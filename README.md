# Case-law block tagger — word-level release

The FLP block tagger labels the structural components of a scanned
case-law opinion: given the opinion text as minimal HTML (`<p>`,
`<blockquote>`, `<em>`, `<sup>`), it tags 12 element classes as
character spans — party, separator, docketnumber, court, attorneys,
judges, datefiled, otherdate, history, disposition, author, heading.

This package reproduces the released model's results on the eval sets
and shows them in a viewer. It holds code only; the eval data ships
separately (see below).

**Model:** [`freelawproject/caselaw-block-tagger-wordlevel`](https://huggingface.co/freelawproject/caselaw-block-tagger-wordlevel),
a ModernBERT-large token classifier, continued from the shipped
`caselaw-block-tagger` on cleaned and human-reviewed gold with
word-level labels (the first token of each word carries the label, the
rest are unsupervised) and entity-escaped twins of the training text
(`&amp;`, the shape the scanning portal sends). Pin the commit you
score (`--revision`). The [model card](https://huggingface.co/freelawproject/caselaw-block-tagger-wordlevel)
on Hugging Face documents the model, its training data and the full
evaluation.

## Differences from the previous model

The previous model was trained with a label on every subword piece and
decoded per token. On real scanned opinions that produced a family of
faults with one root cause: the byte-level tokenizer cuts words into
pieces (`TR|UST`, `R|HO|DE`) and the pieces of one word could carry
different labels. Spans then opened inside the first word of a
paragraph, flipped inside capitalised words, flickered word by word on
long lines, and dispositions came back in fragments. A second gap: the
training text held raw `&` while the scanning pipeline sends `&amp;`,
a form the model had never seen.

| | previous model | this model |
|---|---|---|
| labels at training | every subword piece | the first piece of each word; the rest unsupervised |
| decoding | per token, BIO runs over all pieces | **per word**: read the label of each word's first piece, extend spans to whole words |
| back-to-back same-label spans | separate | shown to the model as one span; it returns one |
| `&amp;` / `&lt;` / `&gt;` | never seen | an entity-escaped twin of every training window that holds `&`, `<` or `>` |
| big-set labels | model-seeded, human-reviewed once | the same pages, re-cleaned by scripted rules and a second human review (about one span in eleven changed: fragments, cross-word cuts, vote-line and disposition conventions) |
| multi-docket captions | 2 training windows with a run of docket lines | plus 650 synthetic captions covering five docket-line shapes |
| post-processing | none | a disposition heal: several disposition spans inside one paragraph become one span over the paragraph |
| initialization | golden-set fine-tune | the previous model |

What stayed the same: the architecture, the input format, the 12
classes and label ids, the context length, and the evaluation sets.

## What changed, and what the worker must do

The previous model labelled every subword piece and the worker decoded
per token, which produced the faults in scanning issue #458: cuts at
the first word of a paragraph (`TR|UST`), inside capitalised words,
word-by-word label flicker on long lines, and dispositions in pieces.
This model is decoded **per word** and a dispositive paragraph is
**healed**:

```python
from tagger.wordlevel import decode_word_spans
from tagger.heal import heal_disposition

enc = tokenizer(text, truncation=False, return_offsets_mapping=True)
ids = model(**batch).logits.argmax(-1)[0].tolist()
names = [config.id2label[i] for i in range(len(config.id2label))]
spans = decode_word_spans(text, enc["offset_mapping"], ids, names)
spans, _ = heal_disposition(text, spans)
```

`decode_word_spans` reads one prediction per word (its first token)
and returns whole-word spans; markup and whitespace never open or split
a span. `heal_disposition` turns two or more disposition spans inside
one `<p>` into one span over the block. Both are pure Python
(`tagger/wordlevel.py`, `tagger/heal.py`, one `re` import each) and go
into the worker next to `handler.py` in place of its per-token
`decode_spans`. The input shape is unchanged. **The new weights must
not be deployed with the per-token decoder**: the continuation pieces
were never supervised.

## Reproducing the results

Requires [uv](https://docs.astral.sh/uv/), the eval-data bundle
(`tagger_eval_data_<date>.tar.gz`, shared separately; it holds the
portal set and the previous model's predictions on it) and a GPU or an
Apple-silicon Mac (under a minute on MPS for the portal set, about four
minutes for all three sets).

```bash
tar -xzf /path/to/tagger_eval_data_<date>.tar.gz   # → data/portal_eval.jsonl, before/portal_eval/
uv sync
uv run pytest                                       # decoder + heal tests
uv run python score.py --revision <commit>          # every data/*.jsonl → out/, prints the table
uv run uvicorn app:app --port 8180                  # → http://127.0.0.1:8180
```

`score.py` runs the model over every `data/<dataset>.jsonl`, decodes
and heals, and writes `out/<dataset>/pred.jsonl`, `heal_log.jsonl` and
`eval_spans.json`. Scores are span-set precision / recall / F1 after
the same normalization on both sides (`tagger/spannorm.py`): **strict**
= content anchoring (edges snapped off whitespace and markup),
**normalized** = edge punctuation trimmed too. The gold is merged first
the way the model sees it (back-to-back same-label spans → one). No
other post-processing.

### Eval sets

| file | what | windows | gold spans | in the bundle |
|---|---|---|---|---|
| `portal_eval.jsonl` | the 13 opinions of scanning issue #458 in the portal's own input shape, reviewed gold | 21 | 363 | yes |
| `val.jsonl` | golden val: hand-annotated born-digital opinions | 39 | 336 | on request |
| `bigset_val.jsonl` | big-set val: OCR'd volumes a3d.316, so3d.398, p3d.515, human-reviewed gold | 142 | 1,535 | on request |
| `dockets_aug_val.jsonl` | synthetic multi-docket captions built from the two val sets (optional check) | 60 | 845 | on request |

One window = one case, up to 8,192 tokens. The scorer picks up
whichever of these files sit in `data/`. The bundle also carries
`before/portal_eval/pred.jsonl`: the previous model's spans on the
portal set, as deployed (per-token decode), the viewer's left pane.
The test sets and the training data never ship.

### Expected scores

Strict span-F1 per class, with overall precision, recall and F1; previous
model → this model (↑ up, ↓ down, = unchanged).

| class | golden val | big-set val | portal set |
|---|---|---|---|
| **all, F1** | 0.968 → 0.999 ↑ | 0.929 → 0.969 ↑ | 0.830 → 0.974 ↑ |
| **all, precision** | 0.941 → 1.000 ↑ | 0.905 → 0.968 ↑ | 0.761 → 0.983 ↑ |
| **all, recall** | 0.997 → 0.997 = | 0.955 → 0.971 ↑ | 0.912 → 0.964 ↑ |
| party | 1.000 → 1.000 = | 0.990 → 0.990 = | 0.963 → 1.000 ↑ |
| separator | 1.000 → 1.000 = | 0.994 → 0.994 = | 1.000 → 1.000 = |
| docketnumber | 1.000 → 1.000 = | 0.949 → 0.986 ↑ | 0.973 → 1.000 ↑ |
| court | 1.000 → 1.000 = | 0.963 → 0.977 ↑ | 0.923 → 1.000 ↑ |
| attorneys | 1.000 → 1.000 = | 0.984 → 0.988 ↑ | 0.741 → 1.000 ↑ |
| judges | 1.000 → 1.000 = | 0.865 → 0.952 ↑ | 0.743 → 0.929 ↑ |
| datefiled | 1.000 → 1.000 = | 0.982 → 0.977 ↓ | 0.889 → 1.000 ↑ |
| otherdate | – | 0.842 → 0.831 ↓ | 1.000 → 1.000 = |
| history | 0.941 → 1.000 ↑ | 0.815 → 0.933 ↑ | 0.486 → 0.963 ↑ |
| disposition | 0.725 → 1.000 ↑ | 0.801 → 0.958 ↑ | 0.800 → 0.815 ↑ |
| author | 1.000 → 1.000 = | 0.968 → 0.976 ↑ | 0.909 → 1.000 ↑ |
| heading | 1.000 → 0.983 ↓ | 0.938 → 0.964 ↑ | 0.804 → 0.961 ↑ |

`score.py` reproduces these numbers from the published weights at
commit `fd0047885ea47e948350320fa6fa9d5b7902166f` (checked 2026-10-30).

## The viewer

One page: every paragraph flagged in scanning issue #458, grouped by
fault type, with the raw output quoted in the issue, what the issue
says is correct, and two panes over the same paragraph: **before**, the
previous model as deployed (from the bundle), and **after**, this model
(from `score.py`). Spans are drawn in the label colours.

- **marks: plain** (default) — each model's own tags; a span only one
  side has (same label and edges on the other side absent) carries a
  dark underline, and the card says how many spans of the flagged
  class differ between the sides.
- **marks: vs gold** — each span judged against the reviewed gold:
  match, same label with other edges (the chip says which edge), gold
  has another label, no gold span here, gold span not returned. The
  card then carries a verdict: fixed (after matches gold), better,
  same, worse, with the difference counts for both sides.
- **edges: strict | normalized** — the rule for the gold comparison,
  the same two the scores use.

## Layout

```
tagger/wordlevel.py   word-level labels + decoder (decode_word_spans, merge_adjacent, …)
tagger/heal.py        heal_disposition
tagger/spannorm.py    span normalization + span-set metrics
score.py              model → out/<dataset>/{pred.jsonl, heal_log.jsonl, eval_spans.json}
app.py, ui/           the issue #458 before/after viewer (FastAPI, no build step)
issue458.json         the flagged paragraphs of scanning issue #458
tests/                decoder and heal tests
make_data_bundle.sh   builds the eval-data bundle (encoder machine only)
```

## Rebuilding the data bundle

On the encoder machine: `bash make_data_bundle.sh /path/to/ai-research-encoder/encoder`
→ `tagger_eval_data_<date>.tar.gz` with the portal set and the
previous model's predictions on it (`train/infer_out/blocktagger_large_bigset/`).
`SETS="val.jsonl bigset_val.jsonl portal_eval.jsonl" bash
make_data_bundle.sh …` adds the other val sets. The script refuses
test files.
