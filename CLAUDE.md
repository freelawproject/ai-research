# CLAUDE.md

## Project Overview

Legal citator pipeline that uses LLMs to analyze appellate court opinions and classify how each cited case is treated (e.g., reversed, distinguished, overruled). Data comes from the CourtListener (CLReplica) Django database. Opinions are pre-processed with `<citedCase>` tags marking citation spans.

## Repository Structure

- `data/` — Shared data files (opinion texts, metadata CSVs, expert labels). Experiment folders symlink here instead of duplicating data. Excluded from git.
- `citator-pipeline/` — Reusable pipeline code (instructions, utils, run scripts). Created after experiments showed Sonnet and Kimi as strong contenders, to consolidate the pipeline and track version changes via git. No data lives here.
- `experiments_MMDD2026/` — Each folder contains the data and results for a specific experiment. Before 0405, each experiment folder also contained its own utils and run scripts. From 0405 onward, all code lives in `citator-pipeline/` and experiment folders contain only data and thin wrapper scripts. Experiments 0402–0405 have legacy symlinks through `experiments_04022026/data/`; new experiments should symlink directly to `data/`.

### Recent Experiments
- `0319` — Baseline evaluation of v225, v318, and v320 instruction versions on 8 expert-annotated cases using Sonnet. v318 improved severity and treatment F1 over v225. v320 was a sidetrack (attempted cost savings by grouping "Cited by" outputs, unsuccessful).
- `0327` — Tested 17 cheaper LLM models on 8 expert-annotated cases for citation extraction and negative treatment detection. Concluded Sonnet 4.6 is the best performer, Kimi K2.5 is a close second.
- `0401` — Compared 5 models on 8 expert-annotated cases using v318 instructions. Sonnet 4.6 was the best overall; Kimi K2.5 had the best treatment F1.
- `0402` — Queried CLReplica DB for ~50 sampled circuit court opinions per circuit (13 circuits, 661 total). This data is shared by later experiments via symlinks.
- `0403` — Built the full pipeline: pagination for long opinions, post-processing (dedup, keyword flagging), and re-evaluation using a second model. Developed v403 prediction instructions and v404 re-evaluation prompt. Kimi K2.5 re-evaluator boosted direction F1 from 0.84 to 0.96 at +6% cost.
- `0404` — Adapted the pipeline for per-court production runs with parameterized `--court` flag. Added "Vacated by" flagging, citation normalization for label matching, and treatment cleanup.
- `0405` — Moved to AWS Bedrock batch inference for running at scale. Experiment folder restructured to be data-only, with code in `citator-pipeline/`.
- `0407` — Two-stage pipeline: Haiku extraction + Kimi classification with section context. Enhanced Kimi classifier prompt with cert denied pattern recognition, citation signals, and implicit distinguishing. Treatment F1 0.54→0.66 macro, direction F1 0.87→0.92, 77% cheaper than 0403 ($0.048 vs $0.210/case). **Recommended production configuration.**
- `0408` — Added Sonnet re-evaluator with section context to the two-stage pipeline. Did not improve quality (treatment F1 0.66→0.64) and added 156% cost overhead. Conclusion: re-evaluation is not beneficial when the Kimi classifier prompt is well-tuned.
- `0518` — First end-to-end batch evaluation of the production two-stage pipeline on the full 0410 benchmark (383 citing clusters, 39 courts). Treatment macro F1 0.38, Cohen's κ 0.58, QWK 0.70. Measured cost $0.047/case batch. Hardened `clean_treatments` canonicalizer; added `derive_severity_direction` postprocess step; added `Modified by` + `Reversed in part; Vacated in part by` to taxonomy (TREATMENT_RANK now 22 entries). DH vs Other split: v305 (Sonnet) wins on Direct History, 0518 wins on Other — motivated the 0529 prompt rewrite that combined both strengths into one single-stage pipeline.
- `0529` — Single-stage Sonnet (`citator` prompt, with kimi_classifier-style structured blocks ported in) on the same 383-cluster 0410 benchmark. Treatment macro F1 0.47, κ 0.71, QWK 0.79 — beats 0518 on every agreement metric. Coverage 327/383 clusters (vs 0518's 321). Cost $35.54 batch ($0.093/case). Built `submit-sonnet` / `collect-sonnet` / `run-sonnet` Bedrock batch path; built optional Kimi K2.5 re-evaluator (`submit-reeval` / `collect-reeval` / `run-reeval`) but found it **net-negative on this Sonnet base** — the improved prompt subsumes most of what reeval was fixing on 0528. Also fixed two latent bugs: `merge_to_labels` empty-citation phantom matches, and `SONNET_MAX_TOKENS = 16384` silent truncation (raised to 64000 = Sonnet 4.6 ceiling). **Best-performing setup measured so far; candidate for adoption pending further validation.**

For detailed metrics across all experiments, see [comparison.md](comparison.md).

## Maintaining CLAUDE.md

- When a change affects project structure, conventions, CLI interfaces, or code style, review and update this file to keep it in sync.
- Examples: new scripts, renamed flags, moved data directories, new treatment types, new experiment patterns.

## Code Style

- **All imports at the top of the file.** Do not use inline/lazy imports inside functions. All `import` and `from ... import` statements must appear at the top of the module, grouped in the standard order: stdlib, third-party, local.

## Key Conventions

- **Instruction versions** are tracked in `citator-pipeline/utils/instructions.py`. The active prompts are: `citator` (single-stage prediction), `reevaluator` (re-evaluation), `haiku_extractor` (two-stage extraction), `kimi_classifier` (two-stage classification). Do not rename these variables.
- **Treatment lists** are defined in the Treatments table below. Any change must be synced to all files listed there.
- **Citation normalization**: The model normalizes reporter names (e.g., "F. Supp." to "F.Supp."). The merge step in `postprocess.py` normalizes both sides to match. Keep this in sync if citation format changes.
- **Incremental saving**: Predictions save to `incremental/{cluster_id}.json` as they complete. Re-evaluation batches save to `reevaluation_incremental/batch_{n}.json`. Resume is opt-in via `--resume` flag.
- **Shared data** lives in `data/` at the repo root (opinion texts, metadata, labels). Experiment folders contain only results (`data/output/`, `data/eval/`). Legacy experiments (pre-0405) still have `data/input/` with symlinks.
- **Benchmark labels** live in `data/benchmark_original/`. The latest version is `0410.csv`. All evaluations default to this file. When the labels are updated, add a new versioned CSV and update the default path in `run_example.py`, `run_two_stage.py`, and `eval_utils.py`.
- **Git ignore**: All experiment `data/` subfolders must be excluded from git. When creating a new experiment folder, add `experiments_MMDD2026/data/` to `.gitignore` immediately.

## Running

All scripts run from `citator-pipeline/`. Input data is read from `--input-dir` (defaults to `../data`), results are written to `--output-dir` (the experiment's `data/` folder). `--txt-path` defaults to `../data/example.txt` (9 expert-annotated cases).

```bash
# Single-stage pipeline (Sonnet + Kimi re-eval) on all 9 examples
python run_example.py --output-dir ../experiments_04062026/data --evaluate

# Two-stage pipeline (Haiku extraction + Kimi classification) on all 9 examples
python run_two_stage.py --output-dir ../experiments_04072026/data --evaluate

# Two-stage batch inference (Haiku Stage 1 + Kimi Stage 2) — the 0518 production config.
# Each run is keyed by a UUID `run_id` and persisted to s3://{bucket}/Citator/runs/{run_id}/.
# S3 is the source of truth between stages; --output-dir is local scratch + final CSVs.
python run_batch.py submit-extraction \
    --output-dir ../experiments_04052026/data --court ca1
# → prints run_id at exit
python run_batch.py submit-classification \
    --output-dir ../experiments_04052026/data --run-id <run_id>
python run_batch.py collect \
    --output-dir ../experiments_04052026/data --run-id <run_id> \
    --labels-file ../data/benchmark_original/0410.csv

# Or end-to-end (chains all three with wait-for-completion between stages)
python run_batch.py run --output-dir ../experiments_04052026/data --court ca1

# Single-stage Sonnet batch inference — the 0529 candidate config.
# IMPORTANT: pass --input-dir explicitly so defaults resolve via the experiment's
# symlinks (otherwise picks up the shared 661-cluster sample metadata).
python run_batch.py run-sonnet \
    --input-dir ../experiments_05292026/data \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv

# Optional Kimi re-evaluator pass (chains off a completed Sonnet run; flags ~12% of
# rows for Kimi to re-classify). Net-negative on the 0529 Sonnet base — not part
# of the candidate setup; left in place for future flag_for_review retuning.
python run_batch.py run-reeval \
    --run-id <sonnet_run_id> \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv

# Smoke test on the synthetic batch_test data (overrides metadata + opinion paths,
# uses short test prompts in place of the production prompts)
python scripts/generate_synthetic_opinions.py
python run_batch.py run \
    --output-dir ../experiments_batch_test/data \
    --metadata-file ../data/batch_test_citing_metadata.csv \
    --opinion-dir ../data/batch_test_opinion_texts \
    --smoke-test
```

## AWS

- Uses Bedrock via `boto3.Session(profile_name="dev-env")` in `us-west-2`
- Batch jobs require `CITATOR_S3_BUCKET` and `CITATOR_BATCH_ROLE_ARN` env vars
- Bedrock batch inference requires ≥100 records per JSONL input file
- Two-stage pipeline:
  - Stage 1 model: `us.anthropic.claude-haiku-4-5-20251001-v1:0` (tool_use)
  - Stage 2 model: `moonshotai.kimi-k2.5` (no tool_use; schema inlined in user prompt)
- Single-stage Sonnet pipeline:
  - Model: `us.anthropic.claude-sonnet-4-6` (tool_use)
  - `SONNET_MAX_TOKENS = 64000` (model's hard ceiling; required for large opinions emitting 60+ cases per page)
- Re-eval pipeline (optional, currently net-negative on 0529 Sonnet base):
  - Model: `moonshotai.kimi-k2.5` with the `reevaluator` prompt
  - Flagged rows batched in groups of 5 (`REEVAL_BATCH_SIZE`)

## Domain Context

- The most common model errors are: cert denied directionality swaps, missing "as recognized by" modifier, "Reversed by" when it should be "Reversed and remanded by", and "Cited by" when the case was actually distinguished.
- "Cited by" is the neutral/default treatment. All other treatments represent some form of negative or procedural action.

## Treatments

This is the canonical treatment list. Any changes here must be reflected in:
- `citator-pipeline/utils/instructions.py` — `citator`, `reevaluator`, and `kimi_classifier` prompts
- `citator-pipeline/utils/bedrock_converse_utils.py` — JSON schema
- `citator-pipeline/utils/postprocess.py` — `TREATMENT_RANK`, `severity_mapping`, and `direction_mapping` (all in one file; `eval_utils.py` imports them)

| Severity | Direct History | Citing Reference | Related Reference |
|----------|---------------|-----------------|-------------------|
| **Stop** | Reversed by | Overruled by | Any treatment + "as recognized by" |
| | Reversed and remanded by | Abrogated by | (e.g., "Overruled as recognized by") |
| | Vacated and remanded by | Questioned by | |
| | Vacated by | | |
| **Warning** | Reversed in part; Vacated in part by | Disapproved by | |
| | Affirmed in part; Reversed in part by | Limited by | |
| | Affirmed in part; Vacated in part by | | |
| **Caution** | Modified by | Criticized by | |
| | Remanded by | Distinguished by | |
| | Cert. granted by | Declined to follow by | |
| **Neutral** | Dismissed by | Cited by | |
| | Cert. denied by | | |
| | Affirmed by | | |

- **Direct History**: The cited case is the immediate case on appeal. The Acting Case is always the Citing Case.
- **Citing Reference**: The Citing Case itself applies treatment to a cited case that is not on appeal.
- **Related Reference**: The Citing Case reports treatment applied by another case. Uses the "as recognized by" modifier combined with any Direct History or Citing Reference treatment.
- **"Cited as recognized by" is not a valid treatment.** If a case is merely cited by another case without negative treatment, the treatment is "Cited by" — never "Cited as recognized by".
