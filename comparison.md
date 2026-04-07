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

## Remaining Challenges
- **Distinguished by vs Cited by**: 65% of remaining misclassifications in 0403. The model struggles to identify implicit distinguishing language when the opinion contrasts facts without using the word "distinguish".

## Future Experiments
- **Two-stage pipeline: Haiku extraction + Kimi treatment analysis.** Split the task into (1) citation extraction and paragraph identification using Haiku 4.5, then (2) treatment classification using Kimi K2.5 on only the relevant paragraphs. Rationale:
  - Haiku has strong extraction completeness (95.30% match rate in 0401, 187 matched in 0327) at the lowest cost ($0.044/case).
  - Kimi has the best treatment F1 (0.46 in 0401, 0.70 flagged F1 in 0327) and is the best re-evaluator (direction F1 0.96 in 0403), but weak on extraction (73.45% match rate).
  - Combined cost could be lower than Sonnet alone (~$0.044 + Kimi per-paragraph cost vs $0.167/case).
