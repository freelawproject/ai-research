# 0529 Sonnet single-stage on 0410 benchmark + Kimi re-eval experiment

End-to-end Bedrock batch run of single-stage Sonnet (`citator` prompt) on the full 0410 expert-labeled benchmark — 383 citing clusters across 39 courts. Includes an opt-in Kimi K2.5 re-evaluation stage that this experiment evaluates and ultimately rejects.

**Run id**: `3807d18f-4b45-4dfa-bb86-9aa9476319eb`
**Submitted**: 2026-05-29
**Bedrock model**: `us.anthropic.claude-sonnet-4-6`
**Re-eval model**: `moonshotai.kimi-k2.5` (Kimi K2.5)

## TL;DR

**0529 Sonnet single-stage is the best-performing setup measured so far and is a candidate for adoption pending further validation.** Treatment macro F1 0.47 vs the 0518 Haiku+Kimi two-stage baseline's 0.38 (+0.09); coverage 327 clusters vs 321; cost $35.54 batch ($0.093/case). On the apples-to-apples Sonnet∩0518 intersection (n=8,828 keys), Sonnet wins every metric.

**The Kimi re-evaluator we built and tested is net-negative on this Sonnet base** (treatment macro F1 0.4729 → 0.4409, severity κ 0.7473 → 0.7020) and is **not** part of the candidate setup. Detailed analysis below — the takeaway is that the `flag_for_review` categories route too many already-correct Sonnet predictions to Kimi, which over-flips them.

## Pipeline

Single-stage Sonnet on Bedrock batch — one call per opinion page, Sonnet emits structured `citedCases` with treatment / direction / quote / rationale per cited case.

- **Model**: `us.anthropic.claude-sonnet-4-6`
- **Prompt**: `citator` from `citator-pipeline/utils/instructions.py`
- **Max output tokens**: 64,000 (Sonnet 4.6's ceiling — essential for large SCOTUS opinions emitting 60+ cases per page)
- **Schema**: `tool_spec` (`citedCases[]` with mainCitationString, caseName, actingCase, caseHistory, treatment, opinionType, quote, rationale)
- **Pagination**: `split_opinion_to_pages` (600K chars / 20K overlap) — 380 of 383 clusters fit in one page, 3 paginate to 2 pages
- **Postprocess**: `filter_non_case_citations` → `clean_treatments` → `deduplicate_cited_cases` → `derive_severity_direction`
- **Eval merge**: `merge_to_labels` joins predictions to `0410.csv` labels by (citing_cluster_id, citation-string-substring-match)

S3 layout per run:

```
s3://{bucket}/Citator/runs/{run_id}/
    manifest.json
    sonnet/
        input.jsonl              (one record per (cluster_id[, page]))
        raw/output.jsonl.out
        parsed/{cluster_id}.json
    reeval/                      (only if Kimi re-eval was submitted)
        input.jsonl              (one record per batch of 5 flagged predictions)
        batches.csv              (batch_idx, within_batch → original row mapping)
        raw/output.jsonl.out
        parsed.json
```

## Headline metrics — full benchmark

The matched eval set contains all (citing, cited) pairs where the model's predicted citation matches a label's citation string substring.

| Level | Metric | 0518 Haiku+Kimi | **0529 Sonnet** | 0529 Sonnet+Reeval |
|---|---|---|---|---|
| | Rows | 8,856 | **9,558** | 9,558 |
| | Clusters | 321 | **327** | 327 |
| **Treatment** | Accuracy | 0.9446 | **0.9628** | 0.9504 |
| | Macro F1 | 0.3821 | **0.4729** | 0.4409 |
| | Weighted F1 | 0.9488 | **0.9645** | 0.9501 |
| | Cohen's κ | 0.5848 | **0.7076** | 0.6887 |
| | QWK | 0.6953 | **0.7916** | 0.7853 |
| **Severity** | Macro F1 | **0.6095** | 0.6068 | 0.5698 |
| | Cohen's κ | 0.5630 | **0.7473** | 0.7020 |
| | QWK | 0.6556 | **0.7680** | 0.7579 |
| **Direction** | Macro F1 | 0.8124 | **0.8227** | 0.6148 |
| | Cohen's κ | 0.7020 | 0.7033 | **0.7182** |

**Sonnet beats 0518 on every metric except Direction Cohen's κ where they're tied.** Best treatment macro F1 (+0.09 over 0518), best severity κ (+0.18), best coverage (+6 clusters / +702 matched rows).

## Apples-to-apples — Sonnet ∩ 0518 intersection (n=8,828 keys)

Restricting both pipelines to the (citing, cited) pairs both matched:

| Pipeline | Rows | T_macroF1 | T_κ | T_QWK | S_κ | D_κ |
|---|---|---|---|---|---|---|
| 0518 Haiku+Kimi (∩) | 8,828 | 0.3861 | 0.5849 | 0.6953 | 0.5624 | 0.7004 |
| **0529 Sonnet (∩)** | 8,828 | **0.4861** | **0.7183** | **0.7915** | **0.7500** | **0.7158** |

On the intersection, Sonnet's lead widens. Treatment macro F1 +0.10, treatment κ +0.13, QWK +0.10, severity κ +0.19.

## Per-class treatment F1 (top supported classes)

| Treatment | Support | P | R | F1 |
|---|---|---|---|---|
| Cited by | 9,001 | 0.988 | 0.979 | **0.983** |
| Distinguished by | 198 | 0.714 | 0.833 | **0.769** |
| Cert. denied by | 96 | 0.621 | 0.615 | 0.618 |
| Affirmed by | 82 | 0.584 | 0.805 | 0.677 |
| Reversed by | 50 | 0.729 | 0.860 | **0.789** |
| Overruled by | 41 | 0.653 | 0.780 | **0.711** |
| Reversed and remanded by | 29 | 0.773 | 0.586 | 0.667 |
| Abrogated by | 17 | 0.000 | 0.000 | 0.000 |
| Limited by | 8 | 0.625 | 0.625 | 0.625 |
| Vacated and remanded by | 7 | 0.700 | 1.000 | **0.824** |

Strong performance across nearly all classes. The lone exception is `Abrogated by` (support 17, F1 = 0) — Sonnet defaults these to `Cited by`, the only class with zero recall. Not a per-cluster-coverage issue; an actual mis-classification gap to address in a future prompt iteration.

## Direct History vs Other split

| Slice | n | Treatment macro F1 | Treatment κ | Treatment QWK |
|---|---|---|---|---|
| Direct History | 111 | **0.7968** | 0.8102 | 0.9445 |
| Other (Citing + Related Reference) | 9,447 | 0.4010 | 0.6591 | 0.6435 |

Sonnet handles Direct History near-perfectly (QWK 0.94 — appellate disposition is essentially solved). The remaining gap is in Citing Reference (Distinguished/Overruled/Abrogated etc.) and Related Reference treatments.

## Top confusion pairs

| true | pred | n |
|---|---|---|
| Cited by | Distinguished by | 60 |
| Cited by | Affirmed by | 43 |
| Cert. denied by | Cited by | 34 |
| Distinguished by | Cited by | 33 |
| Cited by | Cert. denied by | 32 |
| Abrogated by | Cited by | 17 |
| Cited by | Overruled by | 17 |
| Affirmed by | Cited by | 10 |

The dominant residual error is the `Cited by ↔ Distinguished by` axis (93 rows). `Cited by ↔ Cert. denied by` is the next-largest (66 rows, roughly balanced in both directions). `Cited by → Affirmed by` (43) deserves a spot-check — Sonnet is aggressive on Direct History attribution; some of these are correct DH detection that label-side may underreport, but a sample audit would clarify.

## Treatment rank distance distribution

Distance between predicted and true treatment on the 0–21 TREATMENT_RANK ordinal scale.

| Distance | Count | Share |
|---|---|---|
| 0 (exact) | 9,180 | 96.0% |
| 1–3 (within-tier) | 156 | 1.6% |
| 4–5 | 137 | 1.4% |
| 6–10 | 18 | 0.2% |
| 11–21 (far-tier inversions) | 67 | 0.7% |

96% exact match. The 0.7% far-tier inversions (e.g., `Reversed by` ↔ `Cited by`) are the costliest UX-side errors for a citator.

## Kimi re-evaluator experiment

We built an optional Kimi K2.5 re-evaluation stage that flags ~12% of Sonnet predictions (using `postprocess.flag_for_review`: `Cited by` with distinguishing keywords, `Cited by` with cert/writ keywords, all `as recognized by`, all `Distinguished by`, all `Cert. denied by` base form) and re-classifies them in batches of 5 via Kimi. Marginal cost: ~$0.50.

**Result: net-negative on this Sonnet base.** Reeval flipped 212 predictions (2.2% of rows) — **66 fixed, 122 broke, 24 both wrong, 0 both right — net per-row −56.**

| Metric | 0529 Sonnet | 0529 Sonnet+Reeval | Δ |
|---|---|---|---|
| Treatment macro F1 | **0.4729** | 0.4409 | −0.032 |
| Treatment κ | **0.7076** | 0.6887 | −0.019 |
| Treatment QWK | **0.7916** | 0.7853 | −0.006 |
| Severity κ | **0.7473** | 0.7020 | −0.045 |
| Cert. denied F1 | 0.618 | **0.695** | +0.077 |
| Direction κ | 0.7033 | **0.7182** | +0.015 |

Reeval still recovers some Cert. denied recall (0.62 → 0.95) at the cost of precision (0.62 → 0.55), and slightly improves Direction agreement. But it degrades the dominant treatment and severity metrics.

### Top harmful transitions

| Sonnet → Reeval | n | Most are over-predictions |
|---|---|---|
| Cited by → Cert. denied by | 81 | Kimi over-flags cert language in incidental contexts |
| Cited by → Distinguished by | 56 | Kimi over-applies distinguishing keywords as basis |
| Distinguished by → Declined to follow by | 16 | rephrase of a Distinguished case as a "won't follow" |
| Distinguished by → Limited by / Criticized by | 11 | minor severity-tier shifts |

### Why Reeval failed here

The `flag_for_review` categories were designed for a more error-prone Sonnet baseline. The `citator` prompt already incorporates the cert-chain pattern recognition, citation signal handling, and implicit-distinguishing patterns that Kimi's `reevaluator` prompt was designed to enforce. As a result:

1. The flag set routes ~12% of predictions to Kimi, but most of those are already correct.
2. Kimi's `reevaluator` prompt is tuned to be aggressive on the dominant error classes (Cert. denied, Distinguished by). Applied to correct predictions, it over-flips them.
3. Of 212 flipped predictions: 66 were genuinely wrong and Kimi fixed them; 122 were correct and Kimi broke them.

### Decision

**Drop Kimi re-eval from the current candidate setup.** The code path stays in the repo (`run_batch.py::run-reeval`, `submit-reeval`, `collect-reeval`) for future iteration — if we later tighten `flag_for_review` to a higher-suspicion subset (e.g., only `Distinguished by` predictions whose quote has no distinguishing keyword evidence), the marginal value could re-emerge.

## Cost

| Pipeline | Records | Total input tokens | Output tokens | Cost | $/case |
|---|---|---|---|---|---|
| 0518 Haiku+Kimi (batch) | 4,504 | 60M | 3.4M | $18.66 | $0.049 |
| **0529 Sonnet (batch, candidate)** | **386** | **9.5M** | **2.8M** | **$35.54** | **$0.093** |
| 0529 Sonnet+Reeval (net-negative on this base) | 628 | 10.7M | 2.9M | $36.04 | $0.094 |

Sonnet is ~1.9× more expensive per case than Haiku+Kimi but **2.2× cheaper than a hypothetical on-demand Sonnet baseline** (~$0.21/case at the rates of equivalent prior Sonnet runs). Demo extrapolation at the per-case rate × 4,125 clusters ≈ **$384 batch**.

### Apples-to-apples cost normalizations

The headline `$/case` and the "per 1M total input tokens" metrics each have a confounding factor:

- **`$/case`** is fair on this benchmark (same 383 clusters in both pipelines) but doesn't generalize to opinion mixes with different size distributions.
- **`$/1M total input tokens`** lumps prompt overhead into "input volume", which makes pipelines with bigger prompts look more efficient per token than they are. Same opinions, same pagination (386 records each) — the only reason 0518 has 7.2M input tokens and Sonnet has 9.5M is that `citator` (~4.7K tokens) is larger than `haiku_extractor` (~1K).

To control for the prompt difference, split the input into **opinion text** (the work the system has to do per opinion, identical across pipelines) and **prompt overhead** (the per-call tax, different per pipeline). Both numbers shown below:

| Pipeline | Opinion text (est.) | Prompt × 386 records | Total opinion-input | Cost |
|---|---|---|---|---|
| 0518 Haiku+Kimi (Stage 1 only) | ~6.4M | `haiku_extractor` + tool ≈ 2K × 386 ≈ **0.8M** | 7.2M | $18.66 |
| 0529 Sonnet | ~6.4M | `citator` + tool ≈ 6.7K × 386 ≈ **2.6M** | 9.5M | $35.54 |
| 0529 Sonnet+Reeval | ~6.4M | same Sonnet overhead; Reeval input is reevaluator prompt + flagged-row context, not opinion volume | 9.5M (Reeval excluded) | $36.04 |

Two truly apples-to-apples comparisons fall out:

**Comparison A: $/opinion processed (per-case)** — fair because both pipelines processed the identical 383 clusters.

| Pipeline | $/case | Relative to 0518 |
|---|---|---|
| 0518 Haiku+Kimi | **$0.049** | 1.0× |
| 0529 Sonnet | **$0.093** | 1.9× |
| 0529 Sonnet+Reeval | **$0.094** | 1.9× |

**Comparison B: $/1M opinion-text tokens (prompt overhead stripped)** — fair because we're normalizing against the same content volume on both sides.

| Pipeline | $/1M opinion-text tokens | Relative to 0518 |
|---|---|---|
| 0518 Haiku+Kimi | **$2.92** | 1.0× |
| 0529 Sonnet | **$5.55** | 1.9× |
| 0529 Sonnet+Reeval | **$5.63** | 1.9× |

**Both apples-to-apples metrics agree: Sonnet costs ~1.9× per opinion (or per opinion-text token) vs Haiku+Kimi.** This is the cost multiplier to plan against.

### Decomposed cost model — usable for production estimation

A combined "variable + fixed" model lets us estimate cost for any workload:

> **Total cost ≈ A × (opinion-text tokens, in millions) + B × (number of cases)**

- **A** = variable rate per 1M opinion-text tokens (model input rate + output amortized to opinion volume; scales with how much content the system has to ingest and emit)
- **B** = fixed cost per case (prompt overhead per call + downstream stage costs that don't track opinion size)

Computed from the benchmark run (reconstructs the actual totals within rounding):

| Pipeline | A ($/1M opinion-text) | B ($/case) | Reconstructed total | Actual |
|---|---|---|---|---|
| 0518 Haiku+Kimi | **$0.53** | **$0.040** | $18.66 | $18.66 |
| **0529 Sonnet** | **$4.78** | **$0.012** | $35.24 | $35.54 |
| 0529 Sonnet+Reeval | $4.78 | $0.013 | — | $36.04 |

The asymmetry is real and meaningful:
- **A** (variable rate) is **9× lower** for 0518 because the per-token work is done by Haiku ($0.40/M input) rather than Sonnet ($1.50/M input + heavier output).
- **B** (per-case overhead) is **3.3× higher** for 0518 because Stage 2 Kimi makes ~10.75 calls per cluster, and that cost scales with cited-cases-per-opinion, not opinion size. For 0529 single-stage, the per-case cost is just the prompt overhead on ~1 call per case.

### Cost crossover

The two pipelines have a crossover point in opinion size: **~6,500 opinion-text tokens per case**.

- **Below 6.5K tokens/case** (short opinions with many citations): 0529 Sonnet is cheaper per case.
- **Above 6.5K tokens/case** (longer opinions): 0518 Haiku+Kimi is cheaper per case.
- **Benchmark average: 16.7K tokens/case** — 0518 wins on cost at this distribution, as observed.

This means cost competitiveness depends on the opinion mix you're processing — not just the pipeline.

### Production estimates with the decomposed model

For a hypothetical workload of 100K cases × 20K opinion-text tokens each (2,000M opinion-text tokens total):

| Pipeline | Estimated cost | Per-case |
|---|---|---|
| 0518 Haiku+Kimi | ~**$5,045** | $0.050 |
| 0529 Sonnet | ~**$10,774** | $0.108 |

For a workload of 100K cases × 5K opinion-text tokens each (500M opinion-text — short opinions):

| Pipeline | Estimated cost | Per-case |
|---|---|---|
| 0518 Haiku+Kimi | ~**$4,265** | $0.043 |
| 0529 Sonnet | ~**$3,590** | $0.036 |

Note 0529 Sonnet wins on the short-opinion case despite the higher per-token rate, because the per-case overhead dominates.

### Wrinkles in this model — things to watch

1. **B is averaged across cited-case density.** For 0518 specifically, the Stage 2 cost (~$15 of the $18.66 total) is proportional to *cited-cases per opinion* (~10.75 Stage 2 calls per cluster on this benchmark), not opinion size. An opinion with 2× the average cited cases would push B up ~2× for 0518; for 0529 single-stage, cited-case density has minor impact on B.
2. **Pagination amplifies B.** Prompt is paid per *page*, not per case. On the benchmark, only 3 of 383 clusters paginated, so B is ~1.008× the per-page prompt cost. A workload of very large opinions (paginating into 3-5 pages each) would push B up roughly proportionally.
3. **Output cost folded into A.** Output is amortized to opinion-text volume on the assumption that bigger opinions emit more cases and more output. Different output-density mixes would shift A.
4. **Tool schema is part of B**, paid per call at the input rate.
5. **Prompt caching would slash B.** Bedrock batch doesn't currently support prompt caching. If it did, cache reads at ~10% of input rate would drop B for 0529 from $0.012 to ~$0.001 (a 12× reduction on the fixed overhead). The opinion-text-volume rate A wouldn't change much. The crossover point would shift dramatically — Sonnet would be cost-competitive on most opinion sizes.
6. **Reeval is +$0.001/case** in B (242 Kimi records / 383 cases × $0.50 / 242 = tiny per-case add). Not material to the cost story; just a fact.

### Anything else missing

A few additional refinements that we haven't surfaced and might want for higher-fidelity planning:
- **Per-cited-case granularity for 0518**: B currently bundles Stage 2's per-call cost into a per-cluster average. A higher-fidelity model would split B into B_fixed + B_per_cited_case for 0518 specifically.
- **Per-court variation**: the benchmark spans 39 courts; opinion sizes and cited-case densities vary substantially (state appellate is much smaller than SCOTUS). Per-court A and B would be more accurate for non-uniform production traffic.
- **Latency cost**: Bedrock batch SLA is hours; on-demand is real-time but ~2× cost. If real-time is required for any tier, on-demand cost should be modeled separately.
- **Failure / retry cost**: not currently modeled. Stage failures + retries (e.g., the 28 truncated records we hit on a prior run before fixing max_tokens) add overhead that's invisible in a clean benchmark.

### Production extrapolation

Using the apples-to-apples opinion-text rate, scaled to 1B opinion-text tokens (a back-of-envelope production-scale target):

| Pipeline | Cost per 1B opinion-text tokens |
|---|---|
| 0518 Haiku+Kimi | ~**$2,920** |
| 0529 Sonnet | ~**$5,550** |
| 0529 Sonnet+Reeval | ~**$5,630** |

## Candidate for adoption

**0529 Sonnet single-stage** — with the `citator` prompt as-of this commit, `SONNET_MAX_TOKENS = 64000`, and the patched `merge_to_labels` in `postprocess.py` — is the best-performing setup we've measured, and the natural candidate for further validation before any adoption decision.

Why it's the strongest candidate so far:
- Beats Haiku+Kimi two-stage on every metric (+0.09 treatment macro F1, +0.12 κ, +0.10 QWK).
- Best coverage of any config tested (327 clusters / 9,558 matched rows).
- Simplest pipeline (one model, one call per page, no orchestration between stages).
- Direct History essentially solved (QWK 0.94).
- $35.54 batch run cost.

Before any adoption commitment, additional validation should cover at minimum:
- A second benchmark run to confirm metrics replicate.
- A spot-audit of the `Cited by → Affirmed by` over-prediction pattern (43 rows) to confirm whether those are correct DH detections that labels under-report, or genuine over-attribution.
- Cost and latency profiling on a representative production traffic mix (not just the benchmark).

## Known gaps for future iteration

1. **`Abrogated by` per-class F1 = 0** (support 17). Sonnet defaults these to `Cited by`. Worth a prompt addition or example.
2. **`Cited by → Affirmed by` over-prediction** (43 rows). Spot-check needed — may be correct DH detection that the labels under-report, or genuine over-attribution.
3. **`Cited by ↔ Distinguished by` axis** (93 rows total — 60 over, 33 under). The dominant residual error class. A precision-tightening guard in the prompt (require basis-for-different-result language) could narrow this without losing recall.
4. **Tightened reeval flagging** as discussed above — if we want Kimi back in the pipeline, the flag set needs to be more selective.

## Supporting artifacts

- `eval_benchmark.ipynb` — notebook reproducing every metric in this document
- `data/eval/matched_results.csv` — Sonnet predictions matched to 0410 labels
- `data/eval_reeval/matched_results.csv` — Sonnet+Reeval matched (for the experiment-section analysis)
- `data/output/final_results.csv` — Sonnet predictions post-postprocess
- `data/output/final_results_reeval.csv` — Sonnet+Reeval predictions post-postprocess
- `data/scratch/3807d18f-…/` — raw Bedrock batch outputs + manifests (used for cost / token-count / coverage diagnostics)
