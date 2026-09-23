# results/ — the measurements behind `../readme.md`

One folder per training run, holding only what cannot be regenerated: the
evaluation reports and the trainer state. Model weights are **not** here (see
"Where the weights are" below), and neither are the tokenizer and vocabulary
files, which come back with any checkpoint download.

| File | What it is |
|---|---|
| `eval_val.json`, `eval_test.json` | Extraction scores: strict-span P/R/F1, recall of hand-added mentions, rate at which negatives get tagged |
| `coref_val.json`, `coref_test.json` | Linking scores: B-cubed P/R/F1 on gold mentions |
| `coref_test_noeye.json` | Round 5 only: the same linker with the eyecite channel masked off |
| `trainer_state_checkpoint-N.json` | Loss curve and evaluation history up to that checkpoint |
| `config.json` | Model architecture as trained (label count, head shape) |
| `logs/` | Raw stdout of each run, gitignored |

`val` and `test` in these filenames are the trainer's own two evaluation splits,
not the human-verified gold splits; `../readme.md` says which number came from
which.

## The runs

| Run | What it was |
|---|---|
| `extract_large-caselaw` | Round 1 extractor, trained on eyecite-only silver labels |
| `extract_large-caselaw_warm` | **Published extractor.** Round 2, warm-started from round 1, trained on corrected labels |
| `extract_large-caselaw_cold` | Control: the same round-2 labels from the bare base model |
| `link_large-caselaw` | Round 1 linker, silver labels |
| `link_large-caselaw_warm` | Round 2 linker |
| `link_large-caselaw_r3` | Injected-dummies experiment — negative result |
| `link_large-caselaw_r4` | **Published linker.** 128-mention candidate window, encoder chunk recomputed in backward |
| `link_large-caselaw_r5` | eyecite grouping as a linker input channel — negative result |
| `link_large-caselaw_cold` | Control: round-2 labels, no warm start |

## Where the weights are

Every weight file was deleted on 2026-09-22 to reclaim 16 GB. Four checkpoints
were published first, as private Hugging Face repositories:

| Run | Repository |
|---|---|
| `extract_large-caselaw_warm` | `freelawproject/caselaw-citation-extractor` |
| `link_large-caselaw_r4` | `freelawproject/caselaw-citation-linker` |
| `extract_large-caselaw` | `freelawproject/caselaw-citation-warmstart`, `extractor/` |
| `link_large-caselaw` | `freelawproject/caselaw-citation-warmstart`, `linker/` |

The code loads checkpoints from `../runs/<run>/best`, which is gitignored and
starts empty. Download into it:

```bash
huggingface-cli download freelawproject/caselaw-citation-extractor \
    --local-dir runs/extract_large-caselaw_warm/best
```

The other five runs — the two cold-start controls and the three negative
linker experiments — were not published and their weights are gone for good.
They are retrainable from `../model/run_runpod.sh` with the matching `MODE`,
the corpus from `freelawproject/caselaw-citation-corpus`, and a GPU. Their
scores are here and in `../readme.md`; the `cold` control is the one worth
knowing about, because it showed the warm start helps extraction and makes no
difference to linking.
