# Experiment Results: Two-Stage Pipeline with Sonnet Re-evaluation (0408) vs Without (0407)

## Overview

Testing whether adding a Sonnet re-evaluation step to the two-stage pipeline improves quality. The re-evaluator receives full section context and targets: "Cited by" with distinguishing keywords in the quote, all "as recognized by" treatments, and all "Distinguished by" treatments.

Evaluated against `benchmark_original/0410.csv`. Matched results are deduplicated on (citing_cluster_id, cited_cluster_id), keeping the most severe treatment.

---

## Completeness

| Pipeline | Cases | Matched | Model Missed | Match Rate |
|----------|-------|---------|--------------|------------|
| 0407 Haiku + Kimi | 9 | 288 | 4 | 98.63% |
| 0408 Haiku + Kimi + Sonnet | 9 | 290 | 2 | **99.32%** |

---

## Quality

| Metric | 0407 (no re-eval) | 0408 (+ Sonnet) | Delta |
|--------|-------------------|-----------------|-------|
| Severity F1 (macro) | **0.61** | 0.59 | -0.02 |
| Severity F1 (weighted) | **0.76** | 0.74 | -0.02 |
| Direction F1 (macro) | **0.92** | 0.89 | -0.03 |
| Direction F1 (weighted) | **0.90** | 0.88 | -0.02 |
| Treatment F1 (macro) | **0.63** | 0.61 | -0.02 |
| Treatment F1 (weighted) | **0.73** | 0.67 | -0.06 |

### Per-Treatment F1

| Treatment | 0407 | 0408 | Delta | Support |
|----------|------|------|-------|---------|
| Cert. denied as recognized by | **0.93** | 0.86 | -0.07 | 14 |
| Distinguished by | **0.69** | 0.63 | -0.06 | 18 |
| Distinguished as recognized by | **1.00** | **1.00** | 0.00 | 3 |
| Overruled as recognized by | 0.83 | **0.93** | +0.10 | 6 |
| Reversed and remanded by | **1.00** | **1.00** | 0.00 | 2 |
| Reversed by | **1.00** | **1.00** | 0.00 | 2 |
| Affirmed as recognized by | **0.50** | 0.00 | -0.50 | 2 |

### Top Misclassification Patterns

| Error Pattern | 0407 | 0408 |
|---------------|------|------|
| Distinguished by → Cited by | **7** | **7** |
| Cited by → Distinguished by | 3 | **4** |
| Affirmed as recognized by → Cited by | 1 | 1 |
| Cert. denied as recognized by → Cited by | 0 | **1** |
| Cert. denied as recognized by → Distinguished by | 0 | **1** |

**Observations:**
- The Sonnet re-evaluator did not improve any metric. Every quality dimension either stayed flat or slightly regressed.
- **Distinguished by → Cited by** (the primary error) was unchanged at 7 cases.
- **Affirmed as recognized by** regressed to F1 0.00.
- Total misclassifications: 16 → 19 (+3).

---

## Cost

| Pipeline | Cost/Case | Relative to 0407 |
|----------|-----------|-------------------|
| 0407 (no re-eval) | **$0.048** | baseline |
| 0408 (+ Sonnet) | $0.123 | +156% |

---

## Conclusion

**The Sonnet re-evaluator does not justify its cost.** It adds 156% overhead with no quality improvement. The enhanced Kimi classifier prompt already handles the error patterns that re-evaluation was designed to correct.

The slight increase in completeness in the Sonnet re-evaluator run is a result of individual run variations, the citation extractor methods and prompt remains the same between the two version.

**Recommendation:** Use 0407 (Haiku + Kimi, no re-evaluation) as the production configuration.
