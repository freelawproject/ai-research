# CLAUDE.md

## Project Overview

Legal citator pipeline that uses LLMs to analyze appellate court opinions and classify how each cited case is treated (e.g., reversed, distinguished, overruled). Data comes from the CourtListener (CLReplica) Django database. Opinions are pre-processed with `<citedCase>` tags marking citation spans.

## Repository Structure

- `citator-pipeline/` — Reusable pipeline code (instructions, utils, run scripts). Created after experiments showed Sonnet and Kimi as strong contenders, to consolidate the pipeline and track version changes via git. No data lives here.
- `experiments_MMDD2026/` — Each folder contains the data and results for a specific experiment. Before 0405, each experiment folder also contained its own utils and run scripts. From 0405 onward, all code lives in `citator-pipeline/` and experiment folders contain only data and thin wrapper scripts.

### Recent Experiments
- `0319` — Baseline evaluation of v225, v318, and v320 instruction versions on 8 expert-annotated cases using Sonnet. v318 improved severity and treatment F1 over v225. v320 was a sidetrack (attempted cost savings by grouping "Cited by" outputs, unsuccessful).
- `0327` — Tested 17 cheaper LLM models on 8 expert-annotated cases for citation extraction and negative treatment detection. Concluded Sonnet 4.6 is the best performer, Kimi K2.5 is a close second.
- `0401` — Compared 5 models on 8 expert-annotated cases using v318 instructions. Sonnet 4.6 was the best overall; Kimi K2.5 had the best treatment F1.
- `0402` — Queried CLReplica DB for ~50 sampled circuit court opinions per circuit (13 circuits, 661 total). This data is shared by later experiments via symlinks.
- `0403` — Built the full pipeline: pagination for long opinions, post-processing (dedup, keyword flagging), and re-evaluation using a second model. Developed v403 prediction instructions and v404 re-evaluation prompt. Kimi K2.5 re-evaluator boosted direction F1 from 0.84 to 0.96 at +6% cost.
- `0404` — Adapted the pipeline for per-court production runs with parameterized `--court` flag. Added "Vacated by" flagging, citation normalization for label matching, and treatment cleanup.
- `0405` — Moved to AWS Bedrock batch inference for running at scale. Experiment folder restructured to be data-only, with code in `citator-pipeline/`.

For detailed metrics across all experiments, see [comparison.md](comparison.md).

## Key Conventions

- **Instruction versions** are tracked in `citator-pipeline/utils/instructions.py`. The active prompts are named `citator` (prediction) and `reevaluator` (re-evaluation). Do not rename these variables.
- **Treatment lists** are defined in the Treatments table below. Any change must be synced to all files listed there.
- **Citation normalization**: The model normalizes reporter names (e.g., "F. Supp." to "F.Supp."). The merge step in `postprocess.py` normalizes both sides to match. Keep this in sync if citation format changes.
- **Incremental saving**: Predictions save to `incremental/{cluster_id}.json` as they complete. Re-evaluation batches save to `reevaluation_incremental/batch_{n}.json`. Resume is opt-in via `--resume` flag.
- **Experiment data folders** use symlinks to `experiments_04022026/data/` for shared opinion texts and metadata. Do not duplicate data across experiments.

## Running

All scripts require `--data-dir` pointing to the experiment's `data/` folder. Run from `citator-pipeline/` or from an experiment folder (which has thin wrapper scripts).

```bash
# Real-time inference on example cases
python run_example.py --data-dir ../experiments_04032026/data

# Batch inference for a circuit court
python run_batch.py --data-dir ../experiments_04052026/data --court ca1

# Post-processing only (after batch results collected)
python run_batch.py --data-dir ../experiments_04052026/data --court ca1 --postprocess-only
```

## AWS

- Uses Bedrock via `boto3.Session(profile_name="dev-env")` in `us-west-2`
- Batch jobs require `CITATOR_S3_BUCKET` env var (or edit `S3_BUCKET` in `utils/batch_utils.py`)

## Domain Context

- The most common model errors are: cert denied directionality swaps, missing "as recognized by" modifier, "Reversed by" when it should be "Reversed and remanded by", and "Cited by" when the case was actually distinguished.
- "Cited by" is the neutral/default treatment. All other treatments represent some form of negative or procedural action.

## Treatments

This is the canonical treatment list. Any changes here must be reflected in:
- `citator-pipeline/utils/instructions.py` — both `citator` and `reevaluator` prompts
- `citator-pipeline/utils/bedrock_converse_utils.py` — JSON schema
- `citator-pipeline/utils/postprocess.py` — `TREATMENT_RANK`
- `citator-pipeline/utils/eval_utils.py` — `severity_mapping` and `direction_mapping`

| Severity | Direct History | Citing Reference | Related Reference |
|----------|---------------|-----------------|-------------------|
| **Stop** | Reversed by | Overruled by | Any treatment + "as recognized by" |
| | Reversed and remanded by | Abrogated by | (e.g., "Overruled as recognized by") |
| | Vacated and remanded by | Questioned by | |
| | Vacated by | | |
| **Warning** | Affirmed in part; Reversed in part by | Disapproved by | |
| | Affirmed in part; Vacated in part by | Limited by | |
| **Caution** | Remanded by | Criticized by | |
| | Cert. granted by | Distinguished by | |
| | | Declined to follow by | |
| **Neutral** | Dismissed by | Cited by | |
| | Affirmed by | | |
| | Cert. denied by | | |

- **Direct History**: The cited case is the immediate case on appeal. The Acting Case is always the Citing Case.
- **Citing Reference**: The Citing Case itself applies treatment to a cited case that is not on appeal.
- **Related Reference**: The Citing Case reports treatment applied by another case. Uses the "as recognized by" modifier combined with any Direct History or Citing Reference treatment.
- **"Cited as recognized by" is not a valid treatment.** If a case is merely cited by another case without negative treatment, the treatment is "Cited by" — never "Cited as recognized by".
