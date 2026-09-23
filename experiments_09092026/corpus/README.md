# corpus/ — building the labelled citation corpus

Everything that turns CourtListener opinions into the training and evaluation
records the models are fitted on. Run every script from the experiment root
(one level up); data lands in `../data/`, which is gitignored.

The output of this folder is published as
`freelawproject/caselaw-citation-corpus` on Hugging Face.

## The pipeline, in order

| Step | Script | What it does |
|---|---|---|
| 1. Sample | `sample_cl_20k.py`, `sample_cl_20k_round2.py` | Pull opinions out of the CourtListener database. Round 1 took ~20K clusters at random; round 2 took 10K more, oversampling negative-treatment signals and excluding everything round 1 saw. Both run inside the `cl-django` container, not here |
| 2. Exclude | `exclude_ids/*.csv` | The never-sample lists the samplers read, so the two rounds do not overlap and the human-reviewed clusters stay out of training |
| 3. Stage for labelling | `make_r2_annotator.py` | Lay the round-2 sample out as an annotator root the seeding harness can read |
| 4. Watch the seeding | `r2_check.sh` | Poll the batch jobs, fetch finished parts, apply them. Idempotent, safe on a cron |
| 5. Export labels | `export_r2_revised_html.py`, `export_r2_revised_html.sh` | Write the corrected labels back out as revised HTML, the format the dataset builder reads |
| 6. Build records | `build_dataset.py` | Parse silver and gold HTML into one JSONL: text, mention spans, coreference groups, negatives |
| 7. Derive features | `mention_features.py` | Add the per-mention features the linker consumes: citation kind, reporter key, name tokens |

Steps 3 to 5 depend on the seeding harness in
`../../experiments_09022026/triage/`, which does the actual labelling.

## Two labelling passes

Round 1 labels are **silver**: whatever eyecite found in the opinion text, with
no correction. Round 2 labels are those same spans after a language model was
asked to fix them, which is where most of the recall came from. Both passes are
in the built dataset, kept apart by the split each record carries.

## Notes

- `build_dataset.py` and `mention_features.py` are imported by
  `../model/inference.py`, which needs the same feature derivation at prediction
  time that it had at training time. Keep them importable.
- Text normalization is identical for every label source — block tags become
  newlines, tags are stripped, entities unescaped, whitespace collapsed — and
  every offset is into that normalized text, which is what the model is fed.
