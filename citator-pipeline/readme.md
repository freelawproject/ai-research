## Citator Pipeline

Reusable pipeline for identifying cited cases in legal opinions and classifying how each is treated (e.g., reversed, distinguished, overruled). Input is CourtListener opinion text pre-tagged with `<citedCase>` spans by [eyecite](https://github.com/freelawproject/eyecite); output is one record per (citing, cited) case with a treatment label, a verbatim supporting quote, a rationale, and a deterministically derived severity + direction.

- **Taxonomy, conventions, AWS setup, treatment-list sync points** → `../CLAUDE.md`
- **Metrics and cost across experiments** → `../comparison.md` and the per-experiment `../experiments_MMDD2026/` writeups. Numbers are kept there, not here, so this readme doesn't go stale.

### Two pipeline configurations

Both are prompt-driven — the model is given the 43-treatment taxonomy, a decision process, verification steps, and worked examples — and both feed the same postprocessing.

**Single-stage — Sonnet 4.6 (`citator` prompt).** One call per opinion page does extraction *and* classification together, with the full opinion in one attention window. Best-quality configuration measured to date and the current candidate for adoption (see `../comparison.md`); ~1.9× the per-case cost of the two-stage config.

**Two-stage — Haiku 4.5 + Kimi K2.5 (`haiku_extractor` → `kimi_classifier`).** Stage 1 (Haiku) extracts cited cases and the sections they appear in; Stage 2 (Kimi) reads only the section context around each cited case and assigns the treatment. Roughly half the per-case cost of single-stage Sonnet — the cost baseline for corpus-scale runs.

**Optional re-evaluation (`reevaluator` prompt, Kimi K2.5).** A cheap second pass that re-classifies flagged predictions. **Currently not recommended** — on the tuned `citator` prompt it is net-negative (it over-flips already-correct predictions). The code path is retained for future flag-set retuning.

### Postprocessing (shared by all configurations)

`filter_non_case_citations` → `clean_treatments` (canonicalize the model's free-text treatment against the taxonomy) → `deduplicate_cited_cases` (keep the most-negative treatment per `(citing_cluster_id, mainCitationString)`) → `derive_severity_direction` (deterministic lookup) → `merge_to_labels` (match predictions to expert labels with citation normalization) → evaluate. `TREATMENT_RANK`, `severity_mapping`, and `direction_mapping` live in `utils/postprocess.py` — see `../CLAUDE.md` for the files that must stay in sync.

### Prompts

Defined in `utils/instructions.py`. Version history is tracked in git — **do not rename these variables**:

| Variable | Used by |
|---|---|
| `citator` | Single-stage Sonnet (extract + classify) |
| `haiku_extractor` | Two-stage Stage 1 (extraction) |
| `kimi_classifier` | Two-stage Stage 2 (classification) |
| `reevaluator` | Optional re-evaluation pass (not in the candidate setup) |

(`*_short` variants drive the batch smoke test.)

### Run scripts

All scripts run from `citator-pipeline/`. Input is read from `--input-dir` (default `../data`); results are written to `--output-dir` (the experiment's `data/` folder). `--txt-path` defaults to `../data/example.txt`; `--labels` is a filename in `data/benchmark_original/` (default `0410.csv`).

**`run_example.py`** — on-demand single-stage (Sonnet) on the example cases:
```bash
python run_example.py --output-dir ../experiments_XXXX2026/data --evaluate
```
Flags: `--input-dir`, `--txt-path`, `--opinion-dir`, `--labels`, `--evaluate` / `--evaluate-only`, `--resume`.

**`run_two_stage.py`** — on-demand two-stage (Haiku + Kimi) on the example cases:
```bash
python run_two_stage.py --output-dir ../experiments_XXXX2026/data --evaluate
```
Same core flags, plus `--reevaluate` (add a Sonnet re-eval pass), `--postprocess-only`, `--resume`.

**`run_batch.py`** — Bedrock batch inference at scale. Each run is keyed by a UUID `run_id`; S3 is the source of truth between stages and `--output-dir` is local scratch + final CSVs.

```bash
# Two-stage, end-to-end (Stage 1 + Stage 2 + collect):
python run_batch.py run --output-dir ../experiments_XXXX2026/data --court ca1

# Two-stage, stage-by-stage:
python run_batch.py submit-extraction     --output-dir ../experiments_XXXX2026/data --court ca1   # → prints run_id
python run_batch.py submit-classification  --output-dir ../experiments_XXXX2026/data --run-id <run_id>
python run_batch.py collect                --output-dir ../experiments_XXXX2026/data --run-id <run_id> \
    --labels-file ../data/benchmark_original/0410.csv

# Single-stage Sonnet (end-to-end, or submit-sonnet / collect-sonnet):
python run_batch.py run-sonnet --input-dir ../experiments_XXXX2026/data \
    --output-dir ../experiments_XXXX2026/data --labels-file ../data/benchmark_original/0410.csv

# Optional Kimi re-eval on a finished Sonnet run (NOT recommended — see above):
python run_batch.py run-reeval --run-id <sonnet_run_id> \
    --output-dir ../experiments_XXXX2026/data --labels-file ../data/benchmark_original/0410.csv
```
Shared input flags: `--input-dir`, `--metadata-file`, `--opinion-dir`, `--court`, `--num-records`. Use `--smoke-test` (with the synthetic `batch_test` data) and `--no-wait` for testing. Bedrock batch requires ≥100 records per input file.

### Models (AWS Bedrock)

- Single-stage prediction: `us.anthropic.claude-sonnet-4-6`
- Stage 1 extraction: `us.anthropic.claude-haiku-4-5-20251001` (tool_use)
- Stage 2 classification: `moonshotai.kimi-k2.5` (JSON schema inlined in the user prompt; no tool_use)
- Re-evaluation: `moonshotai.kimi-k2.5`

### AWS / S3 configuration

- Bedrock via `boto3.Session(profile_name="dev-env")` in `us-west-2`.
- Batch jobs require the `CITATOR_S3_BUCKET` and `CITATOR_BATCH_ROLE_ARN` environment variables.
- Batch artifacts persist under `s3://{bucket}/Citator/runs/{run_id}/`.

### Incremental saving / resume

On-demand predictions save to `{output_dir}/incremental/{cluster_id}.json` as each completes. Re-run with `--resume` to pick up where an interrupted run left off.
