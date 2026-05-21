# Experiment 0518 — 0410 Benchmark Evaluation

First end-to-end evaluation of the production two-stage pipeline (Haiku 4.5 + Kimi K2.5, Bedrock batch) on the full 0410 expert-labeled benchmark — 383 citing clusters across 39 courts. Prior experiments evaluated only on 8–18 hand-curated cases.

## Pipeline

Two-stage, both stages via AWS Bedrock batch inference (`citator-pipeline/run_batch.py run`):

- **Stage 1 — Haiku 4.5 extraction.** `haiku_extractor` prompt. Annotates the opinion with `[Section Sn]` paragraph-level section markers, identifies each cited case, and reports `mainCitationString`, `caseName`, and `section_ids`. One row per (citing, cited) case.
- **Stage 2 — Kimi K2.5 classification.** `kimi_classifier` prompt. Reads the section context for each cited case (target section + neighbors) and assigns a `treatment` from the 22-entry canonical taxonomy.
- **Postprocess.** `clean_treatments` canonicalizes the LLM's free-text `treatment` against `TREATMENT_RANK` (case fixes, missing-`by`-suffix, Cert. denied aliases, known-invalid forms). `derive_severity_direction` then overwrites `caseHistory` with the deterministic direction implied by the canonical treatment and adds a `severity` column from `severity_mapping`. The 0410 labels' severity + direction columns are 100% derived from `final_treatment` the same way, so predictions and labels are apples-to-apples.

**Run details.** Run id `9f602b16-b55e-4589-83db-6c000e66ce9d`, submitted 2026-05-18. Opinion text + per-cluster metadata pulled from CL by `fetch_benchmark_opinions.py`. 386 Stage 1 records, 4,118 Stage 2 records.

**Measured cost** (from real token counts in raw Bedrock outputs — see `cost_calculation.ipynb`):

| Pipeline | n | Opinion-input tokens | Output tokens | Total cost | $/case | $/5K opinion-input | $/10B opinion-input |
|---|---|---|---|---|---|---|---|
| 0518 Haiku+Kimi, batch | 383 | 7.2M | 3.4M | $18.66 | $0.0487 | $0.0129 | $25,784 |
| v305 Sonnet, on-demand (actual) | 291 | 5.6M | 2.6M | $60.41 | $0.2076 | $0.0538 | $107,626 |
| v305 Sonnet, batch (hypothetical) | 291 | 5.6M | 2.6M | $30.20 | $0.1038 | $0.0269 | $53,813 |

"Opinion-input tokens" = front-of-pipeline content volume (Stage 1 Haiku input for 0518; Sonnet input + cache writes for v305). 0518's total input-side volume across both stages is ~7× v305's (53M vs 7M) because Stage 2 makes ~10.75 calls per cluster, each reading section context + prompt + citation list. Normalizing by opinion-input rather than total input brings per-token and per-case ratios into agreement (~2× between Haiku+Kimi batch and Sonnet batch).

Demo extrapolation at 0518's per-case rate × 4,125 clusters ≈ **$201 batch**.


## Taxonomy additions made for this run

The initial run surfaced 22 unknown treatments (0.17% of 13K rows) from Stage 2 output. Categorized and addressed:

- **Aliases (14 rows)** — `Writ denied` / `Writ refused` / `Appeal dismissed` / `Cert. dism'd as recognized by` → mapped to `Cert. denied` / `Dismissed as recognized by`. Added to `_canonicalize_treatment`.
- **Per-prompt rules (1 row)** — `Cert. pending as recognized by` → `Cited by` (the prompt already says cert. pending is not a treatment).
- **Partial-composite Related Reference (4 rows)** — `[Affirmed|Reversed] in part [on other grounds] as recognized by` → collapse to full RR form (loses partiality info, stays canonical).
- **Missing from taxonomy (3 rows)** — Added `Modified by` (Caution tier, Direct History) and `Reversed in part; Vacated in part by` (Warning tier).

Synced across `TREATMENT_RANK` (now 22 entries), `kimi_classifier` + `citator` prompts, `severity_mapping`, `direction_mapping`, and `CLAUDE.md`. After re-applying postprocess: 0 unknown treatments remain.

## Dataset

`../data/benchmark_original/0410.csv` — 383 citing clusters × ~38 cited cases = 14,536 label rows. After filtering PENDING/REMOVE/MANUAL: **9,740 usable labels**.

| Court bucket | Clusters |
|---|---|
| Federal appellate (`scotus` + `ca*`) | 196 |
| Federal district | 71 |
| State + misc (39 courts total) | 116 |

Top courts: scotus (182), lactapp (55), cand (36), nysurct (19), casd (10), ca2 (9). Long tail of 1–3 clusters across ~30 other state/district courts.

**0410 vs prior 317 benchmark**: 0410 is a strict superset by citing cluster (+92 new, lean state-court / older SCOTUS) and adds `Abrogated by`, `Disapproved by`, `Limited as recognized by` to the taxonomy. ~6% label drift on shared pairs — re-merge older predictions against 0410 labels for apples-to-apples comparison.

## Headline results — full 0518 run

8,836 matched rows (90.9% of usable labels).

| Level | Accuracy | Macro F1 | Weighted F1 | Cohen's κ | QWK |
|---|---|---|---|---|---|
| Treatment | 0.9446 | 0.3821 | 0.9488 | 0.5848 | **0.6953** |
| Severity | 0.9584 | 0.6095 | 0.9629 | 0.5630 | 0.6556 |
| Direction | 0.9784 | 0.8124 | 0.9793 | 0.7020 | — |

Federal-appellate subset (6,375 rows, 188 clusters) is essentially the same +0.01–0.03 on every metric. The model is not artifactually worse on state/district-court text; it has a long tail of minority treatments it never sees.

## v305 (Sonnet 1-stage) vs 0518 (Haiku+Kimi 2-stage)

252-cluster overlap, re-merged against 0410 labels, ~6.2K rows per pipeline.

| Metric | v305 | 0518 |
|---|---|---|
| Treatment macro F1 | **0.4612** | 0.3914 |
| Treatment Cohen's κ | **0.6045** | 0.5810 |
| Treatment QWK | **0.7316** | 0.6937 |
| Severity Cohen's κ | **0.7394** | 0.5515 |
| Direction macro F1 | 0.4780 | **0.8170** |
| Direction Cohen's κ | 0.4573 | **0.7066** |

### Split by Direct History vs Other — the headline

| Metric | v305 DH (n=73) | 0518 DH (n=78) | v305 Other (n=6,084) | 0518 Other (n=6,108) |
|---|---|---|---|---|
| Treatment macro F1 | **0.5611** | 0.3444 | 0.3327 | **0.3943** |
| Treatment QWK | **0.8494** | 0.5679 | 0.4629 | **0.5617** |
| Severity QWK | **0.9035** | 0.5946 | 0.4569 | **0.5467** |
| Direction Cohen's κ | 0.00 | 0.00 | 0.10 | **0.61** |

- **v305 dominates Direct History.** Sonnet excels at appellate-disposition classification (Reversed/Affirmed/Vacated/etc.).
- **0518 dominates Other.** Two-stage Haiku+Kimi handles minority Citing Reference treatments (Distinguished/Overruled/Limited/Criticized) noticeably better.

**Practical implication:** hybrid routing — DH → Sonnet, Other → Haiku+Kimi — is worth exploring.

## 9-opinion eval set vs full benchmark — the gap and what's driving it

| Slice | Treatment macro F1 | Severity macro F1 | Direction macro F1 |
|---|---|---|---|
| 9-set (curated SCOTUS/fed appellate) | **0.6959** | 0.6347 | 0.9733 |
| 252-cluster v305-overlap | 0.3914 | 0.6092 | 0.8170 |
| Full 0518 run | 0.3821 | 0.6095 | 0.8124 |

The 9-set treatment macro F1 is ~2× the full-benchmark number. Three drivers:

1. **Pagination + section-extraction cascade.** Haiku misassigns sections on opinions with uneven paragraph structure (13% of sections exceed 3× target size; one reaches 40,769 chars). Wrong section → Stage 2 gets wrong context → wrong treatment. Five large SCOTUS opinions Haiku dropped entirely contribute 0 predictions. The upcoming stage 1 migration (offset-based section assignment) should address this.
2. **Corpus breadth.** State appellate + federal district courts use reporter conventions and disposition language the prompts weren't tuned on.
3. **Taxonomy support imbalance.** `Abrogated by` / `Disapproved by` / `Questioned by` / `Modified by` / `Remanded by` all sit at per-class F1 = 0 — small support, model defaults to `Cited by`. The 9-set rarely sees these.

How much is bug (1) vs breadth (2) is unknown until the migration lands.

Batch vs on-demand is correctness-equivalent: same pipeline on-demand (0407) vs batch (0518) on the 9-set produces deltas within ±0.05 on every metric.

## Where the errors live

`treatment_rank_distance` = `|TREATMENT_RANK[pred] − TREATMENT_RANK[true]|` on the 0–21 ordinal severity scale.

| Distance | Count | Share |
|---|---|---|
| 0 (exact) | 8,365 | 94.5% |
| 1–3 (within-tier) | 122 | 1.4% |
| 4–5 | 242 | 2.7% |
| 6–10 | 17 | 0.2% |
| 11–21 (far-tier inversions) | 93 | 1.1% |

The dominant error mode is the **`Cited by` ↔ `Distinguished by` axis** (229 of 491 wrong predictions). Model over-predicts `Distinguished by` (175) more than it under-predicts (54).

Forgiving the `Cited by` ↔ `Distinguished by` and `Cited by` ↔ `Cert. denied as recognized by` pairs (287 rows, 3.2%):

| Metric | Baseline | Forgiven | Δ |
|---|---|---|---|
| Treatment Cohen's κ | 0.5848 | **0.8062** | +0.22 |
| Severity Cohen's κ | 0.5630 | **0.8054** | +0.24 |
| Severity macro F1 | 0.6095 | 0.7109 | +0.10 |

If downstream UX treats those pairs as tolerable confusion, real-world quality is closer to ~0.80 Cohen's κ than the headline 0.58.

## Cert. denied

0518 correctly classifies cert. denials (77 predictions matching 75 labels, F1 ≈ 0.95). v305 produces 0 cert-denied predictions because `Cert. denied by` was not in the taxonomy when v305 ran — not a Sonnet gap. Re-running v305 with the current taxonomy would assess Sonnet's actual performance.

## Key findings

1. **Batch ≡ on-demand.** Every metric within ±0.05 on the 9-set.
2. **9-set macro F1 is ~2× the full-benchmark number** (0.70 vs 0.39). Mix of pagination/section bugs (fixable), corpus breadth, and minority-class zero-support.
3. **0518 beats v305 on Other; v305 wins on Direct History.** Hybrid routing is the obvious next move.
4. **Treatment QWK > Severity QWK** — errors cluster close on the ordinal scale rather than randomly.
5. **`Cited by` ↔ `Distinguished by` is the dominant error axis** — fixing it would move treatment Cohen's κ from 0.58 → 0.80 and macro F1 from 0.38 → 0.42. Cohen's κ moves much more because macro F1 still averages in the minority classes that sit at per-class F1 = 0.

## Open questions / follow-ups

- Hybrid routing experiment (DH → Sonnet, Other → Haiku+Kimi)
- Re-run v305 (Sonnet) with the current 22-entry taxonomy to assess its real cert-denied performance
- Per-court F1 breakdown (scotus vs circuit vs lactapp vs district court)

## Supporting Notebook

eval_benchmark.ipynb

| § | Content |
|---|---|
| 1 | Load + re-merge predictions with labels |
| 1b | Treatment rank distance — distribution + top far-distance pairs |
| 2 | Coverage |
| 4–6 | Treatment / severity / direction metrics |
| 7 | Summary table — full 0518 run |
| 7b | Federal appellate top-line |
| 8 | v305 vs 0518 aggregate (252-overlap) |
| 9 | DH vs Other split |
| 10 | Federal appellate v305 vs 0518 |
| 11 | Cert. denied → Cited by collapse |
| 12 | 9-set 0407 (on-demand) vs 0518 (batch) |
| 13 | Pairwise forgiveness on the `Cited by` ↔ `Distinguished by` axis |
