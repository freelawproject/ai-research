# Experiment 0529 — Sonnet single-stage on 0410 benchmark + Kimi re-eval experiment

End-to-end Bedrock batch evaluation of single-stage Sonnet (`citator` prompt) on the full 0410 expert-labeled benchmark — 383 citing clusters across 39 courts. Includes an opt-in Kimi K2.5 re-evaluation stage; the experiment evaluates and ultimately rejects it on this base.

## Pipeline

Single-stage Sonnet via Bedrock batch inference (`citator-pipeline/run_batch.py run-sonnet`).

- **Model**: `us.anthropic.claude-sonnet-4-6`
- **Prompt**: `citator` from `citator-pipeline/utils/instructions.py` (22-entry taxonomy + structured blocks for Cert. denied CRITICAL pattern recognition, Distinguished by implicit-pattern catalog, Citation signals, Distinguished as recognized by)
- **Max output tokens**: 64,000 (Sonnet 4.6's ceiling; required for large SCOTUS opinions emitting 60+ cases per page)
- **Schema**: `tool_spec` from `bedrock_converse_utils.py` (`citedCases[]` with mainCitationString, caseName, actingCase, caseHistory, treatment, opinionType, quote, rationale)
- **Pagination**: `split_opinion_to_pages` (600K chars / 20K overlap)
- **Postprocess**: `filter_non_case_citations` → `clean_treatments` → `deduplicate_cited_cases` → `derive_severity_direction`
- **Eval merge**: `merge_to_labels` joins predictions to 0410 labels by citation-string substring match

**Run id**: `3807d18f-4b45-4dfa-bb86-9aa9476319eb`. 386 Sonnet batch records (383 clusters + 3 paginated), 0 truncated.

## Optional Kimi re-eval pass

Chain a Kimi K2.5 re-evaluation onto a completed Sonnet run to address common Sonnet errors (cert. denied directionality, missed distinguishing, "as recognized by" swaps). Reuses the existing Sonnet inference — no Sonnet re-run.

```bash
python run_batch.py run-reeval \
    --run-id <sonnet_run_id> \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv
```

The flagging logic (`postprocess.flag_for_review`) routes ~12% of Sonnet predictions to re-eval:
1. `Cited by` predictions whose quote has distinguishing keywords (inapplicable, does not apply, not on point, etc.)
2. `Cited by` predictions whose quote has cert/writ keywords (cert. denied, cert. granted, writ denied/refused)
3. All `as recognized by` treatments
4. All `Distinguished by` treatments
5. All `Cert. denied by` base-form treatments

Batch records pack 5 flagged rows each via Kimi K2.5 with the `reevaluator` prompt. Cost ≈ $0.50.

**Result on this run: net-negative** (treatment macro F1 −0.032; severity κ −0.045). See `comparison.md` §"Kimi re-evaluator experiment" for the per-row analysis. The re-eval code path stays in the repo for future iteration if `flag_for_review` is retuned.

## Input data

Symlinked from the 0518 benchmark fetch (identical cluster IDs + opinion texts):

```
data/citing_metadata.csv         -> ../ai-research/experiments_05182026/data/citing_metadata.csv
data/opinion_texts/              -> ../ai-research/experiments_05182026/data/opinion_texts
data/benchmark_cluster_ids.csv   -> ../ai-research/experiments_05182026/data/benchmark_cluster_ids.csv
```

Labels: `../data/benchmark_original/0410.csv` (gitignored shared data).

## Run

From `citator-pipeline/`:

```bash
# Sanity check: metadata symlink resolves to the 383 benchmark rows
python3 -c "
import pandas as pd
df = pd.read_csv('../experiments_05292026/data/citing_metadata.csv')
print('total:', len(df))
print(df.groupby('source').size())
"
# Expected: total 383, source: sampled 383

# End-to-end Sonnet (submit + wait + collect + eval merge).
# IMPORTANT: pass --input-dir explicitly so defaults resolve via the symlinks
# (without it, defaults pick up the shared 661-cluster metadata).
python run_batch.py run-sonnet \
    --input-dir ../experiments_05292026/data \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv

# Optional reeval pass (uses the printed run_id; net-negative on this base — see comparison.md)
python run_batch.py run-reeval \
    --run-id <sonnet_run_id> \
    --output-dir ../experiments_05292026/data \
    --labels-file ../data/benchmark_original/0410.csv
```

## Cost

| Stage | Records | Input tokens | Output tokens | Cost |
|---|---|---|---|---|
| Sonnet batch | 386 | 9.5M | 2.85M | **$35.54** |
| Kimi re-eval (if run) | 242 | 1.2M | 0.1M | ~$0.50 |
| **Total (Sonnet only — candidate setup)** | **386** | **9.5M** | **2.85M** | **$35.54** |

Per-case: $0.093 (Sonnet only). Demo extrapolation at 4,125 clusters: ~$384 batch.

## Headline results

| Level | Metric | 0518 Haiku+Kimi (baseline) | **0529 Sonnet** | 0529 Sonnet+Reeval |
|---|---|---|---|---|
| Treatment | Macro F1 | 0.3821 | **0.4729** | 0.4409 |
| Treatment | Cohen's κ | 0.5848 | **0.7076** | 0.6887 |
| Treatment | QWK | 0.6953 | **0.7916** | 0.7853 |
| Severity | Cohen's κ | 0.5630 | **0.7473** | 0.7020 |
| Direction | Cohen's κ | 0.7020 | 0.7033 | **0.7182** |
| Coverage | rows / clusters | 8,856 / 321 | **9,558 / 327** | 9,558 / 327 |

**Best-performing setup measured here: 0529 Sonnet single-stage, no Kimi re-eval.** Candidate for adoption pending further validation. See `comparison.md` for the detailed analysis, per-class F1, intersection comparison, and rationale.

## Artifacts

- `comparison.md` — three-way detailed comparison + Kimi re-eval analysis
- `eval_benchmark.ipynb` — reproducible source for every metric
- `data/eval/matched_results.csv` — Sonnet predictions matched to 0410 labels
- `data/eval_reeval/matched_results.csv` — Sonnet+Reeval matched
- `data/output/{parsed,final}_results.csv` — Sonnet predictions before/after postprocess
- `data/output/final_results_reeval.csv` — Sonnet+Reeval final predictions
- `data/scratch/3807d18f-…/` — raw Bedrock batch outputs and manifests
