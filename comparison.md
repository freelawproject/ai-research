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

Direct History vs Other split reveals two opposite stories: **v305 dominates Direct History** (treatment macro F1 0.56 vs 0.34; QWK 0.85 vs 0.57), **0518 dominates Other** (treatment macro F1 0.39 vs 0.33). The 0518 readme proposed hybrid routing (DH → Sonnet, Other → Haiku+Kimi); 0529 made hybrid routing unnecessary by lifting Sonnet single-stage above both pipelines on every metric (see below).

### Key Findings (0518)
- **The 9-set overstates the full-benchmark performance by ~2×** (treatment macro F1 0.70 vs 0.39). Driven by a mix of Haiku section-misassignment cascades, broader corpus difficulty, and zero-support minority classes.
- **Dominant error mode is `Cited by` ↔ `Distinguished by`** (229/491 wrong predictions). Forgiving that axis would move Cohen's κ 0.58 → 0.80 and macro F1 0.38 → 0.42.
- **Batch ≡ on-demand**: same pipeline run on-demand (0407) vs batch (0518) on the 9-set produces deltas within ±0.05 on every metric.
- **v305 producing 0 cert. denied predictions is not a Sonnet gap** — `Cert. denied by` wasn't in the taxonomy when v305 ran.

## Full 0410 Benchmark Evaluation (0529)

Single-stage Sonnet on the same 383 0410-benchmark citing clusters. New `citator` prompt incorporates kimi_classifier-style structured blocks (Cert. denied CRITICAL pattern recognition, Distinguished by implicit-pattern catalog, Citation signals, Distinguished as recognized by). `SONNET_MAX_TOKENS` raised to 64,000 to eliminate truncation. See `experiments_05292026/comparison.md` for the full write-up and `experiments_05292026/eval_benchmark.ipynb` for reproducibility.

### Headline metrics (full 0410 benchmark)

| Pipeline | Rows | Clusters | T_macroF1 | T_κ | T_QWK | S_κ | D_κ |
|---|---|---|---|---|---|---|---|
| 0518 Haiku+Kimi (baseline) | 8,856 | 321 | 0.3821 | 0.5848 | 0.6953 | 0.5630 | 0.7020 |
| **0529 Sonnet (candidate)** | **9,558** | **327** | **0.4729** | **0.7076** | **0.7916** | **0.7473** | 0.7033 |
| 0529 Sonnet + Kimi reeval (rejected) | 9,558 | 327 | 0.4409 | 0.6887 | 0.7853 | 0.7020 | 0.7182 |

### Apples-to-apples — Sonnet ∩ 0518 intersection (n=8,828)

| Pipeline | T_macroF1 | T_κ | T_QWK | S_κ | D_κ |
|---|---|---|---|---|---|
| 0518 Haiku+Kimi (∩) | 0.3861 | 0.5849 | 0.6953 | 0.5624 | 0.7004 |
| **0529 Sonnet (∩)** | **0.4861** | **0.7183** | **0.7915** | **0.7500** | **0.7158** |

0529 Sonnet's lead widens on the intersection: +0.10 macro F1, +0.13 κ, +0.10 QWK. The DH vs Other gap from 0518 is closed — 0529 Sonnet wins both slices (DH macro F1 0.80, Other macro F1 0.40).

### Cost

| Pipeline | Total | $/case | $/1M opinion-text tokens (decomposed model) |
|---|---|---|---|
| 0518 Haiku+Kimi (batch) | $18.66 | $0.049 | A=$0.53/M opinion-text + B=$0.040/case |
| **0529 Sonnet (batch)** | **$35.54** | **$0.093** | **A=$4.78/M opinion-text + B=$0.012/case** |
| 0529 Sonnet+Reeval | $36.04 | $0.094 | A=$4.78 + B=$0.013/case |

Decomposed model: `Cost ≈ A × M_opinion_text_tokens + B × n_cases`. A captures opinion-volume-driven variable cost; B captures per-case overhead (prompts, downstream stage calls). **Pipeline cost crossover ≈ 6,500 opinion-text tokens/case**: below, 0529 Sonnet is cheaper per case; above, 0518 Haiku+Kimi is cheaper. Benchmark avg 16.7K tokens/case → 0518 cheaper at this distribution. Cost competitiveness depends on opinion-size mix.

### Key Findings (0529)

- **0529 Sonnet beats 0518 on every agreement metric.** Treatment macro F1 +0.09, κ +0.12, QWK +0.10. Severity κ +0.19. Direction roughly tied.
- **Cert. denied F1 jumped from 0.17 to 0.62** — the new CRITICAL pattern block in `citator` does the work that Kimi was doing on 0528-era runs. No re-eval needed.
- **DH dominance preserved.** Treatment macro F1 0.80 on DH (vs 0518's 0.33); QWK 0.94. Direct History is essentially solved.
- **Other-slice macro F1 0.40** — matches 0518 on the same slice, while keeping the DH win. The hybrid-routing recommendation from 0518 is no longer needed.
- **Kimi re-eval on this base is net-negative** (−0.032 macro F1; 66 fixed / 122 broke per row). `flag_for_review` categories were tuned against 0528's weaker Sonnet base; on 0529 they route too many correct predictions to Kimi, which over-flips them. Code path retained for future flag-set retuning.
- **Two latent bugs fixed**: `merge_to_labels` empty-citation phantom matches (was inflating DH counts by ~30× on the 0528 run); `SONNET_MAX_TOKENS = 16384` silent truncation on large SCOTUS opinions (raised to 64K = Sonnet 4.6 ceiling).

## Remaining Challenges (as of 0529)

- **`Cited by` ↔ `Distinguished by`** — still the dominant residual error class (93 rows in 0529 Sonnet: 60 over-predictions, 33 under-predictions). The implicit-distinguishing patterns ported into the `citator` prompt narrowed this but didn't eliminate it. A precision-tightening guard (require basis-for-different-result language) is a candidate next iteration.
- **`Cited by → Affirmed by`** — newly prominent in 0529 (43 rows, up from 26 on the 0528 buggy run). The revised `citator` prompt is more aggressive on Direct History attribution; some of these are correct DH detections that 0410 labels may under-report, but a spot-audit is needed to confirm before assuming they're all gains.
- **`Abrogated by` per-class F1 = 0** (support 17 in 0529). Sonnet defaults these to `Cited by`. The `citator` prompt has no example for `Abrogated by` — worth a future prompt addition.
- **Validation re-run** — 0529 is currently a single benchmark run; one replication run is a precondition for any adoption decision.
- **Pagination + section-extraction cascade** (0518 two-stage only) — Haiku misassigns sections on opinions with uneven paragraph structure (13% of sections > 3× target size). Not relevant to 0529 single-stage, which has no section-extraction step.
