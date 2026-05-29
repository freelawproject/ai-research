# Phased Eyecite Architecture — Retrospective (2026-05-28)

## Verdict

The phased eyecite-anchored architecture proposed on 2026-05-19 and implemented through 2026-05-28 (`citator_pipeline_migration_plan.md`) is **abandoned**. The approach was to use CL's eyecite output as a deterministic citation extractor and pre-tag every citation in the opinion text, then layer LLM stages on top for grouping (Phase 2), disposition (Phase 3), audit (Phase 4), and classification (Phase 5). Phase 1 worked. Phase 2 did not, and the failure modes show the strategy is unsound, not just under-tuned.

## What was tried

- **Phase 1** — `experiments_05262026/`. Tree-walk CL's `html_with_citations` to produce per-opinion `tagged_text` with `<cited id group>` tags + an `id_to_metadata_map` and `groups` map. Validated 383/383 structurally clean on the 0410 benchmark (48,991 tags).
- **Phase 2** — `experiments_05272026/`. Haiku 4.5 receives the tagged text + asks the model to confirm / split / merge the parser's `group` hints into distinct cited cases, plus scan for untagged occurrences (formal cites + narrative refs like "the Quarles Court held"). Output is per-cluster JSON: list of `cited_cases` each with a `mainCitationString` / `caseName` / `accepted_ids[]` / `untagged_occurrences[]`. Postprocess does two-pass merge + audit logs.

The Phase 2 prompt was rewritten six times across one day. The schema was iterated three times (rejected_ids added, then removed; parallelCitationString added; rejected_ids removed). Pagination was changed (150K → 20K-token chunks). Postprocess gained id-conflict resolution and Phase 1 fallback. Nothing produced acceptable quality on the SCOTUS-class dense opinions that drove the validation effort.

## Failure modes observed

1. **Cross-chunk inconsistency.** Same id, same opinion, same prompt — the model includes it in chunk A and omits it in chunk B. The model assigns id N to *case X* in one chunk and to *case Y* in another. These are not minor disagreements: on 117935, Nixon references got absorbed into the Powell case row by the postprocess winner rule because Powell had more occurrences. See `experiments_05272026/chunking_quality_analysis.md`.

2. **Whole-opinion attention dilution.** Single-call architecture on dense opinions (755 ids in 117935) silently omits 90–109 ids per run despite forbidding silent omission in the prompt. The model treats some legitimate pin cites / `Id.` / bare reporter cites as not-citations even when explicitly told they are.

3. **Group/case inference is the hard part.** Eyecite reliably extracts citations and chains them via `data-id`, but reliably deciding whether two reporter cites refer to the same case or whether a pin cite belongs to an earlier full cite still requires holding the whole opinion in mind. The LLM does this unreliably whether the input is one 90K chunk or six 20K chunks. Postprocess can patch holes (Phase 1 fallback) but cannot reconstruct cross-chunk semantic agreement.

4. **Case fragmentation.** Even within a single call, the model emits 122 cited_cases for an opinion that has ~95 real cases — small variations in canonical names across chunks defeat the Pass 2 normalized-name merge, leaving fragments behind. Tightening the canonical matcher would mask the symptom but not the underlying flakiness.

5. **Cost compounding.** 117935 needed 13 LLM calls under the chunked architecture (vs 2 whole-opinion). Demo extrapolation against 4,125 clusters lands at ~3× the budget of the 0518 production pipeline.

## Why this is a strategic failure, not a tuning failure

The architecture's premise was that pre-tagging citations with a deterministic extractor would simplify the LLM's job from "find AND group citations" to "just group the pre-found citations." The empirical result is that grouping pre-tagged citations is roughly as hard as extracting them — and the LLM is unreliable at it for the same reason it's unreliable at extraction: long-range dependencies, dense reference patterns, and the inability to consistently apply legal-citation conventions across a long document.

The hardest cases (dense SCOTUS opinions with hundreds of pin cites referring to a handful of earlier-cited cases) are also the most-cited cases in the corpus. Quality must be high there. Postprocess heuristics (Phase 1 group_id fallback, name-aware merge, conflict resolution) recover from individual model errors but cannot fix the model's systemic inconsistency.

## What's still valid from this work

- **`fetch_cl_data.py` + the cl_fetch JSON shape** — useful infrastructure for any pipeline that needs CL opinion text + UnmatchedCitation rows. (`experiments_05262026/fetch_cl_data.py`.)
- **`assemble_tagged_text.py` and the Phase 1 tree-walk + safety-net design** — produces correct, validated tagged text with offsets. Usable as-is by any downstream that wants pre-tagged input.
- **`render_tagged.py`** — Phase 1 artifact viewer; useful for any inspection of CL opinion text + citation positions.
- **0519 eyecite vs Haiku extraction comparison** — confirms eyecite has higher recall than the Haiku extraction step in the 0518 production pipeline (15,604 vs 13,044 citations on the benchmark). Still true; informs whatever pipeline comes next.
- **0410 benchmark + 0518 production baseline metrics** — unchanged points of comparison.
- **The Phase 1 spot-check finding about duplicate `010combined` records (28% of clusters)** — a structural fact about the CLReplica DB content, independent of pipeline architecture. Will need to be addressed by whatever pipeline replaces this.

## What did not work and should not be revived without reframing

- LLM as case-grouper on pre-tagged citations (Phase 2 as specified).
- The phased read-from-disk-between-stages pipeline shape (Phases 2/3/4 parallel, then Phase 5). Useful as orchestration discipline; not coupled to the eyecite anchoring decision.
- The "router not editor" framing for the grouping prompt — sound idea, but the model isn't reliable enough at routing under attention pressure.

## Possible next directions (not prescriptive)

These are options to evaluate, not a recommended plan:

- **Return to single-stage LLM extraction + classification** (the 0407/0518 production architecture) and target the 0518 quality gaps directly. Known issues: 0518 F1 = 0.38 macro on the 22-treatment taxonomy; DH/Other split shows Sonnet better on DH, Haiku better on Other.
- **Sonnet 4.6 on dense opinions only** — capacity routing. Cheaper to test than rebuilding the architecture; isolates the "is this attention dilution or something else?" question.
- **Fine-tuned encoder for citation extraction + grouping** (issue #62) — replaces both eyecite and the LLM's grouping role. Was deferred earlier; this retrospective doesn't change that, but raises the priority if dense-opinion grouping remains a blocker.
- **Drop case-level grouping entirely; classify per-occurrence** — every `<cited>` tag gets its own treatment label, and a postprocess step collapses by (volume, reporter, page). Sidesteps the LLM's grouping unreliability by not asking it to group.

## Status of artifacts

The Phase 1 (0526) and Phase 2 (0527) code, the experimental data, and the 0519 comparison work all stay in the repo as-is — they're the record of what was tried. The migration plan (`citator_pipeline_migration_plan.md`) is marked **ABANDONED** at its header; the Phase 2 prompt and schema in `citator-pipeline/utils/` are no longer the target pipeline.

For day-of details, see:
- `experiments_05272026/chunking_quality_analysis.md` — what specifically broke in the chunked architecture
- `experiments_05262026/readme.md` — Phase 1 implementation (the part that worked)
- `citator_pipeline_migration_plan.md` decisions log — chronological record of the architectural choices that led here
