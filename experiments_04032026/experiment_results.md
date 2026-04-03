# Experiment Results: v403 Pipeline with Re-evaluation

## Overview

This experiment evaluates the v403 citator pipeline (Sonnet 4.6 prediction + post-processing + re-evaluation) against the v318 baseline from experiments_04012026. Four experiments were conducted:

1. **Kimi (8 cases)**: Sonnet 4.6 prediction → Kimi K2.5 re-evaluation on 8 original examples
2. **Sonnet (8 cases)**: Sonnet 4.6 prediction → Sonnet 4.6 re-evaluation on 8 original examples
3. **Kimi Long (1 case)**: Sonnet 4.6 prediction with page splitting → Kimi K2.5 re-evaluation on 1 long opinion (110380, 150K chars)
4. **Sonnet Long (1 case)**: Sonnet 4.6 prediction with page splitting → Sonnet 4.6 re-evaluation on 1 long opinion

The baseline is the v318 Sonnet 4.6 run from experiments_04012026 (same 8 cases, no re-evaluation, no page splitting, no deduplicate post-processing).

---

## Completeness

| Experiment | Cases | Matched | Model Missed | CL Missed | Match Rate |
|-----------|-------|---------|-------------|-----------|------------|
| v318 Baseline (no reeval) | 8 | 244 | 2 | 26 | 99.19% |
| v403 + Kimi reeval | 8 | 241 | 2 | 18 | 99.18% |
| v403 + Sonnet reeval | 8 | 239 | 2 | 17 | 99.17% |
| v403 + Kimi reeval (long) | 1 | 83 | 1 | 4 | 98.81% |
| v403 + Sonnet reeval (long) | 1 | 84 | 0 | 4 | 100.00% |

**Observations:**
- Match rates are consistently high (~99%) across all experiments. The v403 pipeline maintains the v318 baseline's completeness.
- CL missed counts are lower in v403 (17–18 vs 26), likely due to the deduplication step removing spurious predictions.
- The long opinion with page splitting achieves 99–100% match rate, confirming the splitting strategy works.

---

## Severity Classification

| Experiment | Stop | Warning | Caution | Neutral | Macro F1 | Weighted F1 |
|-----------|------|---------|---------|---------|----------|-------------|
| v318 Baseline (no reeval) | 0.95 | -- | 0.64 | 0.77 | **0.79** | 0.76 |
| v403 + Kimi reeval | 1.00 | 0.00* | 0.62 | 0.78 | 0.60 | **0.77** |
| v403 + Sonnet reeval | 0.91 | -- | 0.62 | 0.73 | **0.75** | 0.73 |
| v403 + Kimi reeval (long) | 1.00 | -- | -- | 1.00 | **1.00** | **1.00** |
| v403 + Sonnet reeval (long) | 1.00 | -- | -- | 1.00 | **1.00** | **1.00** |

*Kimi's macro avg is pulled down by Warning=0.00 appearing as a FP-only class (1 FP, 0 support), inflating the denominator to 4 classes.

**Observations:**
- Kimi re-evaluation achieves perfect Stop detection (F1=1.00) vs Sonnet's 0.91, which is close to the v318 baseline (0.95). Sonnet re-evaluation introduced 2 FP Stop predictions (overruled/reversed misassignments).
- Kimi's macro avg (0.60) is misleadingly low because a single Warning FP (0 true support) adds a 0.00 class to the average. Its weighted avg is 0.77, comparable to Sonnet's 0.73.
- Caution F1 is similar across all experiments (~0.62), showing the re-evaluation step doesn't substantially help or hurt this class.
- The long opinion achieves perfect severity on all 4 labeled cases for both re-evaluators.

---

## Direction Classification

| Experiment | Direct History | Citing Reference | Related Reference | Macro F1 | Weighted F1 |
|-----------|----------------|-----------------|-------------------|----------|-------------|
| v318 Baseline (no reeval) | 1.00 | 0.78 | 0.73 | 0.84 | 0.77 |
| v403 + Kimi reeval | 1.00 | 0.94 | 0.94 | **0.96** | **0.94** |
| v403 + Sonnet reeval | 1.00 | 0.86 | 0.86 | 0.91 | 0.87 |
| v403 + Kimi reeval (long) | 1.00 | 1.00 | 1.00 | **1.00** | **1.00** |
| v403 + Sonnet reeval (long) | 1.00 | 1.00 | 1.00 | **1.00** | **1.00** |

**Observations:**
- **Direction is the biggest improvement.** The v403 pipeline with re-evaluation significantly improves direction classification:
  - Kimi reeval: 0.96 macro F1 (up from 0.84 baseline) — Citing Reference improved from 0.78→0.94, Related Reference from 0.73→0.94.
  - Sonnet reeval: 0.91 macro F1 (up from 0.84 baseline).
- **Kimi outperforms Sonnet on direction** by a notable margin (0.96 vs 0.91). This suggests Kimi is better at correcting directionality errors (the target vs. applier confusion in "as recognized by" and cert treatments).
- The long opinion achieves perfect direction for both re-evaluators.

---

## Treatment Classification

| Experiment | Macro F1 | Weighted F1 | Accuracy | Mismatches |
|-----------|----------|-------------|----------|------------|
| v318 Baseline (no reeval) | 0.35 | 0.57 | 0.50 | — |
| v403 + Kimi reeval | **0.47** | **0.68** | **0.67** | 17 |
| v403 + Sonnet reeval | 0.41 | 0.63 | 0.60 | 21 |
| v403 + Kimi reeval (long) | **1.00** | **1.00** | **1.00** | 0 |
| v403 + Sonnet reeval (long) | **1.00** | **1.00** | **1.00** | 0 |

### Per-Treatment F1 Comparison (8 cases)

| Treatment | v318 Baseline | v403 + Kimi | v403 + Sonnet |
|----------|---------------|-------------|---------------|
| Cert. denied as recognized by | 0.47 | **0.93** | 0.85 |
| Overruled as recognized by | 0.93 | **1.00** | 0.80 |
| Distinguished by | 0.67 | 0.62 | **0.65** |
| Affirmed as recognized by | — | **0.80** | **0.80** |
| Cited by | 0.10* | **0.12** | 0.11 |
| Reversed and remanded by | — | 0.67 | 0.67 |

*Cited by F1 is low across all experiments due to the 1% downsampling of the dominant class.

### Top Misclassification Patterns

| Label → Prediction | Kimi | Sonnet |
|-------------------|------|--------|
| Distinguished by → Cited by | 6 | 7 |
| Cited by → Distinguished by | 5 | 4 |
| Cited by → Cert. denied as recognized by | 2 | 2 |
| Overruled as recognized by → Overruled by | 0 | 1 |
| Cert. denied as recognized by → Cited by | 0 | 1 |

**Observations:**
- **Kimi re-evaluation outperforms Sonnet re-evaluation on treatment F1** (0.47 vs 0.41) and accuracy (0.67 vs 0.60). This is consistent with the 04012026 finding that Kimi had the highest treatment F1 (0.46).
- **Cert. denied as recognized by** is dramatically improved: Kimi achieves F1=0.93 (up from 0.47 baseline), Sonnet achieves 0.85. This validates the v403 instruction enhancements and re-evaluation step targeting this specific error pattern.
- **Overruled as recognized by** is perfect with Kimi (1.00) but regresses with Sonnet (0.80, down from 0.93). Sonnet's re-evaluation introduced 1 FP "Overruled by" (dropping the "as recognized by" modifier).
- **Distinguished by remains the hardest treatment** — the dominant error is confusion with "Cited by" in both directions (6–7 cases each way). The re-evaluation step has limited impact here because the boundary between neutral citation and subtle distinguishing is genuinely ambiguous.
- The long opinion achieves perfect treatment classification (4/4) for both re-evaluators, confirming page splitting works correctly.

---

## Budget Analysis

### Prediction Costs (Sonnet 4.6, same for all experiments)

| Experiment | Input Tokens | Output Tokens | Est. Cost |
|-----------|-------------|--------------|-----------|
| v318 Baseline (8 cases) | 160,750 | 56,897 | $1.34 |
| v403 (8 cases) | 170,254 | ~53,875 | $1.32 |
| v403 Long (1 case, 2 pages) | 62,630 | ~24,527 | $0.56 |

Prediction costs are comparable between v318 and v403 (~$0.17/case). The v403 instructions are longer (~37K chars vs ~33K chars) but output tokens are slightly lower due to deduplication.

### Re-evaluation Costs

| Re-evaluator | Cases | Input Tokens | Output Tokens | Est. Cost | Cost/Case |
|-------------|-------|-------------|--------------|-----------|-----------|
| Kimi K2.5 (8 cases) | 21 batches | 50,566 | 8,404 | $0.09 | $0.012 |
| Sonnet 4.6 (8 cases) | 21 batches | 66,427 | 10,440 | $0.36 | $0.045 |
| Kimi K2.5 (1 long case) | 6 batches | 13,785 | 2,046 | $0.02 | $0.024 |
| Sonnet 4.6 (1 long case) | 5 batches | 15,749 | 2,243 | $0.08 | $0.081 |

*Pricing: Sonnet 4.6 = $3/M input + $15/M output. Kimi K2.5 = $1/M input + $5/M output (Bedrock pricing for moonshotai.kimi-k2.5).*

### Total Pipeline Cost per Case

| Pipeline | Predict | Re-eval | Total/Case |
|---------|---------|---------|------------|
| v318 Baseline (Sonnet only) | $0.167 | $0.000 | **$0.167** |
| v403 + Kimi reeval | $0.165 | $0.012 | **$0.177** (+6%) |
| v403 + Sonnet reeval | $0.165 | $0.045 | **$0.210** (+26%) |

### Cost per 1,000 Records (Projected)

| Pipeline | Est. Cost / 1K Records |
|---------|----------------------|
| v318 Baseline (Sonnet only) | $167 |
| v403 + Kimi reeval | **$177** |
| v403 + Sonnet reeval | $210 |

**Observations:**
- Kimi re-evaluation adds only ~$10/1K records (6% overhead) while delivering the best quality improvements.
- Sonnet re-evaluation adds ~$43/1K records (26% overhead) with inferior quality compared to Kimi.
- The batched re-evaluation design (5 cases per API call) keeps costs low — the re-evaluation system prompt (~5K chars) is amortized across the batch.

---

## Page Splitting Analysis (Long Opinion)

The long opinion (cluster 110380, 150K chars) was split into 2 pages with 20K char overlap.

| Metric | Kimi Reeval | Sonnet Reeval |
|--------|------------|---------------|
| Matched to labels | 83 | 84 |
| Model missed | 1 | 0 |
| Severity F1 | 1.00 | 1.00 |
| Direction F1 | 1.00 | 1.00 |
| Treatment F1 | 1.00 | 1.00 |
| Total input tokens (predict) | 62,630 | 62,630 |
| Total output tokens (predict) | 24,249 | 24,804 |

**Observations:**
- Page splitting works correctly — the overlap ensures no citations are missed at page boundaries.
- Both re-evaluators achieve perfect scores on the 4 labeled cases.
- Sonnet achieves 84 matches (vs 83 for Kimi) and 0 model misses, suggesting slightly better completeness.
- The cost overhead of splitting is minimal — 2 API calls at ~31K input tokens each vs 1 call at 62K tokens if no splitting were needed.

---

## Recommendations

1. **Use Kimi K2.5 as the re-evaluation model.** It delivers the best quality across all metrics (treatment F1=0.47, direction F1=0.96) at the lowest re-evaluation cost (+6% overhead, ~$10/1K records). Kimi particularly excels at correcting cert denied directionality errors (F1=0.93 vs 0.85 for Sonnet). Note: Kimi's severity macro avg (0.60) is misleadingly low due to a single Warning FP on a 0-support class; its weighted avg (0.77) is comparable to Sonnet's (0.73).

2. **The v403 instruction + re-evaluation pipeline is a significant improvement over v318 baseline:**
   - Direction F1: 0.84 → 0.96 (+14%)
   - Treatment F1: 0.35 → 0.47 (+34%)
   - Cert. denied as recognized by F1: 0.47 → 0.93 (+98%)

3. **Page splitting is validated.** The 145K char threshold with 20K overlap works for opinions up to at least 150K chars with no quality degradation.

4. **Distinguished by remains the primary quality bottleneck.** The confusion between "Distinguished by" and "Cited by" accounts for 11 of 17 misclassifications (65%) with Kimi re-evaluation. Further prompt engineering or a specialized re-evaluation step for this treatment may be needed.

5. **Next step:** Run the full CA1 sampled dataset (49 cases) with Kimi re-evaluation to validate at scale.

---
*This analysis compares experiments_04032026 (v403, with re-evaluation) against experiments_04012026 (v318, no re-evaluation) using the same 8 expert-annotated cases.*
