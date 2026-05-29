# Chunking Architecture — Quality Analysis (2026-05-28)

## Verdict

Chunking dense opinions into 20K-token pieces (5K overlap) and routing every Phase 1 id without rejection introduces **correctness regressions that outweigh the gains.** The hypothesis that smaller chunks reduce attention dilution is technically supported, but cross-chunk inconsistency produces worse downstream output than the dilution it was meant to fix. Recommend reverting to whole-opinion calls and pursuing dense-opinion quality through a different lever (e.g., model capacity / Sonnet routing).

## Setup

Realtime Phase 2 on three clusters spanning the density range:

| Cluster | Type | Phase 1 tags | Notes |
|---|---|---:|---|
| 100887 | small civil | 16 | Easy baseline |
| 9557725 | trial court | 147 | Medium; OCR noise |
| 117935 | SCOTUS | 755 | Pathological density driver |

Two architectures compared:
- **Old**: 150K-token chunks, 2K overlap, FFD bin-packing, `rejected_ids` schema field, scan-first prompt.
- **New**: 20K-token chunks, 5K overlap, one-opinion-per-call, no rejection, tightened SCAN step, postprocess id-conflict resolution + Phase 1 fallback.

## Metrics

| Cluster | Calls | Cases | Occurrences | Untagged + | Unrouted | Id conflicts | Output tokens |
|---|---:|---:|---:|---:|---:|---:|---:|
| 100887 (old) | 1 | 14 | 15 | 0 | 0 explicit / 0 silent | n/a | 1,575 |
| 100887 (new) | 1 | 14 | 15 | 0 | 0 | 0 | 1,578 |
| 9557725 (old) | 1 | 97 | 146 | 0 | 0 / 1 | n/a | 12,572 |
| 9557725 (new) | 2 | 100 | 147 | 0 | 0 | 3 | 15,872 |
| **117935 (old)** | **2** | **95** | **650** | **0** | **0 / 109** | **n/a** | **21,760** |
| **117935 (new)** | **13** | **122** | **778** | **27** | **17 (all fallback-recovered)** | **29** | **41,619** |

## What got better

1. **Untagged scanning fires.** 117935 produced 27 untagged accepted (vs 0 before) — the tightened SCAN step worked. 9557725 still produced 0; the issue is eyecite-rich opinions, not the prompt.
2. **No more lost ids.** Combined unrouted-id fallback + id-conflict resolution means every Phase 1 id ends up in `cited_cases`. Was 109 silently dropped on 117935; now 17 routed by fallback, none lost.

## What got worse

### 1. Id-conflict winner rule misroutes ids ⚠️

The "winner = most occurrences" tiebreak is wrong when the dominant case is dominant *because* it absorbed minor cases in some chunks. On 117935:

**Nixon misrouted to Powell.** id=179 has `citation_string = "Nixon"`. One chunk (Powell-dominated context) routed it to *Powell v. McCormack*; another chunk correctly routed it to *Nixon v. United States*. Postprocess picked Powell (202 occurrences) over Nixon (2 occurrences). Same for id=671. Result: Powell case now has 217 occurrences, **104 of which are the literal string "Powell"** — almost certainly correct most of the time, but the inflated count came partly from absorbing Nixon references.

**U.S. Term Limits, Inc. v. Hill ↔ Thornton conflation.** Six pin-cite ids (`872 S.W.2d, at 364`, `id., at 276`, etc.) got conflict-resolved to *Hill* (49 occurrences) over *Thornton* (14 occurrences). Hill is the Arkansas state court ruling; Thornton is the SCOTUS appeal. They are distinct opinions in the same litigation. The model correctly distinguished them in some chunks and conflated them in others; the winner rule consistently picked the larger one regardless of which was right.

**Conclusion**: Phase 1's `group_id` is more trustworthy than the model's cross-chunk consensus when the model contradicts itself. The conflict-resolution rule should prefer the case that matches Phase 1's grouping, not the case with the most occurrences. But this only re-introduces eyecite's grouping authority, which we already had with a single-call architecture.

### 2. Case fragmentation

117935 went from 95 → 122 cases, a 28% increase. Distribution of occurrence counts per case:

| Occurrences per case | # cases |
|---:|---:|
| 1 | 18 |
| 2 | 54 |
| 3-4 | 27 |
| 5-9 | 14 |
| 10-49 | 7 |
| 77+ | 2 |

54 two-occurrence cases is a lot of small fragments. Some are likely genuine (cases briefly cited); others are likely the same case the model named slightly differently across chunks (e.g., one chunk says `"U.S. Term Limits"`, another says `"U.S. Term Limits, Inc. v. Hill"`, postprocess's canonical-merge misses the partial match). Pass 2 merges on exact normalized `(mainCit, caseName)`; near-miss spellings fall through.

### 3. Cost explosion on dense clusters

117935 cost: **13 calls, ~$0.25 each at Haiku batch pricing** (vs ~$0.05 under the old architecture). Demo extrapolation (4,125 clusters) with similar density distribution lands closer to $400-600 batch vs the prior $200 estimate — and the bulk of that increase comes from a small number of dense outliers, not the average.

### 4. Inconsistent model behavior across chunks

The Powell unrouted ids (149, 184, 359, 366, 396, 426, 485, etc.) are all literally `"Powell"` or `"Id."` or `"395 U. S., at 528"` — i.e., short cites that should obviously go to the Powell case. The model included them in some chunks (the Powell case has 217 occurrences from 13 chunks) and omitted them in others. Same prompt, same model, same case context — different answers depending on which 20K-token slice they appeared in. Phase 1 fallback recovered them, but the inconsistency is a quality signal: chunking destabilizes the model's per-id decisions.

### 5. Pre-existing issue surfaced — statute citations

11 cases on 117935 have `caseName: null` because eyecite tagged statutes (`Fla. Stat. §§97.041`, `Ga. Code Ann. §§21-2-2`, `39 U.S.C. §3210`, `1 Stat. 73`, etc.) as citations. Statutes are not cases; downstream Phase 5 will attempt treatment classification on them and likely fail. This is **not chunking-specific** — the old architecture had the same issue, masked because those ids went into a single combined case row. Worth filing separately.

## What we can keep regardless of chunking decision

| Change | Keep | Rationale |
|---|:---:|---|
| `rejected_ids` field removed from schema + prompt | ✓ | eyecite precision is trusted; rejection only caused over-rejection bugs |
| Tightened SCAN step in the prompt | ✓ | Worked: 27 untagged additions on 117935 |
| Postprocess Step 2c (id-conflict detection) | ✓ as audit | Useful for surfacing bugs; needs better resolution rule |
| Postprocess Step 2d (Phase 1 fallback) | ✓ | Preserves all ids even when the model misbehaves |
| Name-aware Pass 1 (chimera fix) | ✓ | Independent improvement |
| `paginate_and_call` simplifications (no FFD) | ✓ | Cleaner code; combined-vs-non-combined isolation is desirable regardless |

## What to revert / rethink

1. **`MAX_OPINION_TOKENS` back to ~150K** (or keep one-opinion-per-call but with a much larger threshold). The 20K chunking is where the regressions live.
2. **Drop `CHUNK_OVERLAP_TOKENS` 5K back to 2K** (or further) — overlap is only useful when chunks happen, and chunks should be rare under a larger threshold.
3. **Id-conflict winner rule.** "Most occurrences wins" is the wrong heuristic. If we keep this code path, prefer the case that aligns with Phase 1's `group_id` over the case with more occurrences. But ideally we shouldn't need this rule at all — single-call architecture eliminates the conflict source.

## Recommended next direction

**Revert to whole-opinion calls for non-pathological opinions.** Keep the prompt + schema improvements. For the ~5–10 truly dense opinions in the 0410 benchmark (those with >300 tags), consider:

- **Sonnet 4.6 routing**: 1 call to a bigger model. ~3× the per-call cost on those clusters only. No architecture change. Tests whether attention dilution is real or whether the original 117935 omissions were prompt-related (the prompt rewrite alone may already have fixed them).
- **Citation-neighborhood extraction**: keep whole-opinion in spirit but strip body prose between citations. Cuts tokens without splitting context. Earlier analysis was lukewarm because 117935 is so dense neighborhoods barely shrink — but it still helps for moderately dense opinions.

The chunking hypothesis was sound in theory (smaller windows = more focused attention). The practical reality is that legal citations have **long-range dependencies** (a pin cite at character 80,000 refers to a full cite at character 5,000), and chunking breaks them. Postprocess can patch holes via `group_id` fallback, but it cannot reconstruct cross-chunk semantic agreement.

## Files affected by this analysis

- `citator-pipeline/utils/paginate_and_call.py` — `MAX_OPINION_TOKENS`, `CHUNK_OVERLAP_TOKENS`
- `citator-pipeline/utils/postprocess_offsets.py` — `resolve_id_conflicts` rule (or function deletion if reverting fully)
- `experiments_05192026/citator_pipeline_migration_plan.md` — Phase 2 spec + decisions log
- This document tracks why the 2026-05-28 second-pass changes are being reconsidered.
