# Experiment Results: Two-Stage Pipeline (0407) vs Single-Stage (0403)

## Overview

Comparing the two-stage pipeline (Haiku extraction + Kimi classification) against the single-stage baseline (Sonnet prediction + Kimi re-evaluation) on 9 expert-annotated cases. The 0407 pipeline uses enhanced prompts with improved cert denied pattern recognition, citation signal guidance, and implicit distinguishing detection.

Evaluated against `benchmark_original/0410.csv`. Matched results are deduplicated on (citing_cluster_id, cited_cluster_id), keeping the most severe treatment.

---

## Completeness

| Pipeline | Cases | Matched | Model Missed | Match Rate |
|----------|-------|---------|--------------|------------|
| 0403 Sonnet + Kimi | 9 | 289 | 3 | **98.97%** |
| 0407 Haiku + Kimi | 9 | 288 | 4 | 98.63% |

Both pipelines achieve ~99% match rate.

---

## Quality

| Metric | 0403 Sonnet + Kimi | 0407 Haiku + Kimi | Delta |
|--------|-------------------|-------------------|-------|
| Severity F1 (macro) | 0.52 | **0.61** | +0.09 |
| Severity F1 (weighted) | 0.62 | **0.76** | +0.14 |
| Direction F1 (macro) | 0.83 | **0.92** | +0.09 |
| Direction F1 (weighted) | 0.77 | **0.90** | +0.13 |
| Treatment F1 (macro) | 0.42 | **0.63** | +0.21 |
| Treatment F1 (weighted) | 0.49 | **0.73** | +0.24 |

### Per-Treatment F1

| Treatment | 0403 | 0407 | Delta | Support |
|----------|------|------|-------|---------|
| Cert. denied as recognized by | 0.48 | **0.93** | +0.45 | 14 |
| Distinguished by | 0.48 | **0.69** | +0.21 | 18 |
| Distinguished as recognized by | 0.00 | **1.00** | +1.00 | 3 |
| Reversed and remanded by | 0.67 | **1.00** | +0.33 | 2 |
| Reversed by | 0.80 | **1.00** | +0.20 | 2 |
| Reversed and remanded as recognized by | 0.00 | **1.00** | +1.00 | 1 |
| Overruled as recognized by | **0.93** | 0.83 | -0.10 | 6 |
| Affirmed as recognized by | **1.00** | 0.50 | -0.50 | 2 |

### Top Misclassification Patterns

| Error Pattern | 0403 | 0407 |
|---------------|------|------|
| Distinguished by → Cited by | 11 | **7** |
| Cert. denied as recognized by → Cited by | **9** | 0 |
| Cited by → Distinguished by | 3 | 3 |
| Distinguished as recognized by → Cited by | **2** | 0 |

**Key improvements in 0407:**
- **Cert. denied**: F1 0.48 → 0.93 (+0.45). Enhanced prompt nearly eliminated this error class.
- **Distinguished by**: F1 0.48 → 0.69 (+0.21). Citation signal guidance cut false negatives from 11 to 7.
- **Treatment overall**: Macro F1 0.42 → 0.63 (+0.21), weighted F1 0.49 → 0.73 (+0.24).
- **Total misclassifications**: 31 → 16 (48% reduction).

---

## Cost

| Pipeline | Cost/Case | Relative |
|----------|-----------|----------|
| 0403 Sonnet + Kimi | $0.210 | baseline |
| 0407 Haiku + Kimi | **$0.048** | **-77%** |

---

## Remaining Challenges

1. **Distinguished by → Cited by** (7 cases) remains the top error.
2. **Affirmed as recognized by** (F1 0.50) — narrative procedural history chains are harder in section context.
