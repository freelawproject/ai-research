# Citator Experiment Comparison

All results are on the same 8 expert-annotated cases unless noted. Metrics are macro F1 unless noted.

## Instruction Version Progression (Sonnet 4.6)

| Experiment | Instructions | Re-evaluator | Match Rate | Severity F1 | Direction F1 | Treatment F1 | Cost/Case |
|------------|-------------|--------------|------------|-------------|--------------|--------------|-----------|
| 0319 | v225 (baseline) | — | — | 0.61 | **0.90** | 0.58 | — |
| 0319 | v318 | — | — | 0.66 | 0.81 | **0.72** | — |
| 0319 | v320 (sidetrack) | — | — | **0.75** | 0.89 | 0.64 | — |
| 0401 | v318 | — | **99.19%** | **0.79** | 0.84 | 0.35 | $0.167 |
| 0403 | v403 | Kimi K2.5 | 99.18% | 0.77* | **0.96** | **0.47** | **$0.177** |
| 0403 | v403 | Sonnet 4.6 | 99.17% | 0.75 | 0.91 | 0.41 | $0.210 |

*Kimi severity weighted F1 is 0.77; macro is 0.60 due to a single Warning FP inflating the denominator.

**Notes:**
- 0319 and 0401 used different evaluation setups (0319 used parsed_results before dedup; 0401 used deduplicated results), so severity/treatment F1 are not directly comparable across these two experiments.
- Direction F1 dropped from v225 (0.90) to v318 (0.81) because v318 introduced cert treatments that expanded the Related Reference class, making it harder to classify correctly.
- v320 attempted to reduce cost by grouping "Cited by" outputs but did not achieve savings. It was abandoned in favor of v318.
- v403 kept the same prediction prompt as v318 with enhanced verification steps. The major gains came from the re-evaluation step.

## Key Improvements by Version

### v225 → v318 (0319)
- Added "Cert. denied by" and "Cert. granted by" treatments
- More comprehensive instructions (26K → 33K chars)
- Severity improved (0.61 → 0.66), treatment improved (0.58 → 0.72)
- Direction regressed (0.90 → 0.81) due to new cert treatment classes

### v318 → v403 (0401 → 0403)
- Added mandatory verification for "as recognized by" and cert treatments
- Added "Acting Case ≠ Target Case" rule
- Expanded "Distinguished by" language patterns
- Post-processing: deduplication, keyword flagging, re-evaluation
- Direction improved significantly (0.84 → 0.96 with Kimi)
- Treatment improved (0.35 → 0.47 with Kimi)

### v403 → v404 (0403 → 0404)
- Re-evaluation prompt enhanced with "Reversed by" vs "Reversed and remanded by" guidance
- "Vacated by" added to flagging for re-evaluation
- No change to prediction prompt

## Model Comparison (0327 — Binary Flagging Task)

17 models tested on citation extraction and negative treatment flagging:

| Model | Matched Labels | Flagged F1 | Cost/Case |
|-------|---------------|------------|-----------|
| Claude Sonnet 4.6 | **183/183** | 0.63 | $0.138 |
| Kimi K2.5 | 163/183 | **0.70** | $0.037 |
| Claude Haiku 4.5 | 187/183* | 0.39 | $0.050 |
| Gemini 3 Flash | 149/183 | 0.61 | **$0.016** |

*Haiku matched more than total labels due to false positive matches.

## Model Comparison (0401 — Full Classification with v318)

| Model | Match Rate | Severity F1 | Direction F1 | Treatment F1 | Cost/Case |
|-------|-----------|-------------|--------------|--------------|-----------|
| **Sonnet 4.6** | **99.19%** | **0.79** | **0.84** | 0.35 | $0.167 |
| Kimi K2.5 | 73.45% | **0.79** | 0.79 | **0.46** | $0.060 |
| Haiku 4.5 | 95.30% | 0.68 | 0.53 | 0.26 | **$0.044** |
| Gemini 3.1 Pro | 76.03% | 0.42 | 0.62 | 0.28 | $0.223 |

## Per-Treatment F1 Progression (Sonnet 4.6, 8 cases)

| Treatment | v318 (0401) | v403+Kimi (0403) | v403+Sonnet (0403) |
|----------|-------------|------------------|-------------------|
| Cert. denied as recognized by | 0.47 | **0.93** | 0.85 |
| Overruled as recognized by | 0.93 | **1.00** | 0.80 |
| Distinguished by | **0.67** | 0.62 | 0.65 |
| Affirmed as recognized by | — | **0.80** | **0.80** |
| Reversed and remanded by | — | **0.67** | **0.67** |
| Cited by | 0.10 | **0.12** | 0.11 |

## Two-Stage Pipeline Results (0407, 0408)

Evaluated against `benchmark_original/0410.csv`.

### Pipeline Comparison (9 expert-annotated cases)

| Pipeline | Match Rate | Severity F1 (macro/weighted) | Direction F1 (macro/weighted) | Treatment F1 (macro/weighted) | Cost/Case |
|----------|-----------|------------------------------|-------------------------------|-------------------------------|-----------|
| 0403 Sonnet + Kimi re-eval | 98.97% | 0.52 / 0.62 | 0.83 / 0.77 | 0.42 / 0.49 | $0.210 |
| **0407 Haiku + Kimi** | 98.63% | **0.61 / 0.76** | **0.92 / 0.90** | **0.63 / 0.73** | **$0.048** |
| 0408 Haiku + Kimi + Sonnet re-eval | 99.32% | 0.59 / 0.74 | 0.89 / 0.88 | 0.61 / 0.67 | $0.123 |

### Per-Treatment F1 Progression

| Treatment | v403+Kimi (0403) | Two-stage (0407) | +Sonnet re-eval (0408) | Support |
|----------|------------------|------------------|------------------------|---------|
| Cert. denied as recognized by | 0.48 | **0.93** | 0.86 | 14 |
| Distinguished by | 0.48 | **0.69** | 0.63 | 18 |
| Distinguished as recognized by | 0.00 | **1.00** | **1.00** | 3 |
| Overruled as recognized by | **0.93** | 0.83 | **0.93** | 6 |
| Reversed and remanded by | 0.67 | **1.00** | **1.00** | 2 |
| Reversed by | 0.80 | **1.00** | **1.00** | 2 |
| Affirmed as recognized by | **1.00** | 0.50 | 0.00 | 2 |

### Key Findings
- **0407 is the recommended production configuration.** It achieves the best quality across severity, direction, and treatment metrics while being 77% cheaper than 0403.
- The enhanced Kimi classifier prompt (cert denied patterns, citation signals, implicit distinguishing) was the main quality driver, not the pipeline architecture change.
- **Re-evaluation (0408) does not help** when the classifier prompt is well-tuned. It added 156% cost overhead with no quality improvement. Total misclassifications increased from 16 to 19.
- **Remaining challenge:** Distinguished by vs Cited by confusion accounts for 7 of 16 misclassifications (44%). These are cases with subtle implicit distinguishing language.

## Full 0410 Benchmark Evaluation (0518)

First end-to-end batch run on all 383 0410-benchmark citing clusters (39 courts; ~6.4M tokens; batch cost ~$18.66 / $0.047 per case). See `experiments_05182026/readme.md` for the full write-up.

| Slice | Accuracy | Treatment macro F1 | Treatment Cohen's κ | Treatment QWK | Severity macro F1 | Direction macro F1 |
|---|---|---|---|---|---|---|
| 9-set (curated, batch) | — | **0.6959** | 0.7941 | **0.8705** | 0.6347 | 0.9733 |
| Full 0518 run (383 clusters) | 0.9446 | 0.3821 | 0.5848 | 0.6953 | 0.6095 | 0.8124 |
| Federal-appellate slice (188 clusters) | 0.9318 | 0.3951 | 0.5920 | 0.7205 | 0.6222 | 0.8245 |

### v305 (Sonnet 1-stage) vs 0518 (Haiku+Kimi 2-stage) — 252-cluster overlap

| Metric | v305 | 0518 | Winner |
|---|---|---|---|
| Treatment macro F1 | 0.4612 | 0.3914 | v305 |
| Treatment Cohen's κ | 0.6045 | 0.5810 | v305 |
| Treatment QWK | 0.7316 | 0.6937 | v305 |
| Severity Cohen's κ | 0.7394 | 0.5515 | v305 |
| Direction macro F1 | 0.4780 | 0.8170 | **0518** |
| Direction Cohen's κ | 0.4573 | 0.7066 | **0518** |

Direct History vs Other split reveals two opposite stories: **v305 dominates Direct History** (treatment macro F1 0.56 vs 0.34; QWK 0.85 vs 0.57), **0518 dominates Other** (treatment macro F1 0.39 vs 0.33). Hybrid routing — DH → Sonnet, Other → Haiku+Kimi — is worth exploring.

### Key Findings (0518)
- **The 9-set overstates the full-benchmark performance by ~2×** (treatment macro F1 0.70 vs 0.39). Driven by a mix of Haiku section-misassignment cascades, broader corpus difficulty, and zero-support minority classes.
- **Dominant error mode is `Cited by` ↔ `Distinguished by`** (229/491 wrong predictions). Forgiving that axis would move Cohen's κ 0.58 → 0.80 and macro F1 0.38 → 0.42.
- **Batch ≡ on-demand**: same pipeline run on-demand (0407) vs batch (0518) on the 9-set produces deltas within ±0.05 on every metric.
- **v305 producing 0 cert. denied predictions is not a Sonnet gap** — `Cert. denied by` wasn't in the taxonomy when v305 ran.

## Remaining Challenges
- **Distinguished by vs Cited by**: 44% of remaining misclassifications in 0407, similarly dominant in 0518. The model struggles to identify implicit distinguishing language when the opinion contrasts facts without using the word "distinguish".
- **Affirmed as recognized by**: Narrative procedural history chains are harder to detect in section context (F1 0.50 in 0407 vs 1.00 in 0403).
- **Long-tail minority treatments** (`Abrogated by`, `Disapproved by`, `Questioned by`, `Modified by`, `Remanded by`): per-class F1 = 0 in 0518; small support, model defaults to `Cited by`.
- **Pagination + section-extraction cascade**: Haiku misassigns sections on opinions with uneven paragraph structure (13% of sections > 3× target size); upstream errors propagate to wrong-context Stage 2 classifications. Targeted for the offset-based stage 1 migration.
