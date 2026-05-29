# Experiment 0527 — Citator Pipeline Migration, Phase 2

> **Status (2026-05-28): ABANDONED.** Phase 2 was implemented and iterated over the course of one day (six prompt rewrites, three schema iterations, two pagination architectures) and judged unviable. See `chunking_quality_analysis.md` for the specific failure modes that closed out the architecture and `../experiments_05192026/phased_eyecite_retrospective.md` for the broader verdict on the phased eyecite-anchored approach.

## What was attempted

Phase 2 of the citator pipeline migration: given a Phase 1 tagged artifact (one cluster's opinions as plain text with `<cited id="N" group="gM">` tags), have Haiku 4.5 group the tagged ids into distinct cited cases, confirm or revise the parser's group hints, and scan for citations the parser missed. Output: per-cluster JSON of `cited_cases` each carrying `mainCitationString`, `caseName`, `parallelCitationString`, `accepted_ids[]`, and `untagged_occurrences[]`. Postprocess merges across calls, runs audit logging, and (in the final iteration) applies Phase 1 group_id fallback for any ids the model omitted.

## Files

```
experiments_05272026/
├── run_phase2.py                # Driver: realtime + batch + --postprocess-only
├── render_prepped.py            # Viewer for prepped batch input (per-cluster + index)
├── render_grouped.py            # Viewer for Phase 2 grouped output (heavily featured)
├── test_phase2_synthetic.py     # 53 unit tests on synthetic artifacts
├── chunking_quality_analysis.md # Quality findings that closed out the architecture
├── readme.md
└── data/                        # Gitignored
    ├── grouped/{cluster_id}.json
    ├── grouped_*.jsonl                  # untagged, unrouted, id_conflicts, invalid, casename_warnings
    ├── phase2_calls/{cluster_id}.json   # Serialized CallInputs (for --postprocess-only)
    ├── phase2_raw_realtime/{cid}_call_{i}.json  # Per-call inputs + emissions
    ├── phase2_input.jsonl               # Bedrock batch input
    ├── phase2_prep_html/                # render_prepped output
    └── grouped_html/                    # render_grouped output

../citator-pipeline/utils/
├── paginate_and_call.py         # Token estimation, tag-safe chunking, prepare_calls
├── postprocess_offsets.py       # Validate + two-pass merge + id-conflict resolution + Phase 1 fallback
├── bedrock_converse_utils.py    # citation_grouping_schema + tool spec
├── instructions.py              # citation_grouping prompt (~5K tokens)
└── batch_utils.py               # build_citation_grouping_record + extract_citation_grouping_emission
```

## Iterations across this experiment

Six prompt rewrites and three schema iterations across one day, on three smoke clusters (100887 small, 9557725 trial-court, 117935 dense SCOTUS):

1. **First pass** (single-call, 150K-token chunks, `rejected_ids` schema field, scan-first workflow): produced 90/755 over-rejections of legitimate citations on 117935; zero untagged additions across all 3 clusters.
2. **Second pass** (same architecture, prompt + Pass 1 chimera fix in postprocess): same bug shifted from 90 explicit rejections to 109 silent omissions. Zero untagged still.
3. **Third pass** (20K-token chunks, 5K overlap, one-opinion-per-call, `rejected_ids` removed, tightened SCAN step, postprocess Phase 1 fallback, postprocess id-conflict resolution): untagged scanning fired (27 additions on 117935), unrouted ids dropped (109 → 17 fallback-recovered). But chunking introduced new failure modes — see `chunking_quality_analysis.md`.

## Final state of the code

- Pagination: 20K-token chunks with 5K overlap, one opinion per call (no FFD packing).
- Schema: `cited_cases[]` only (no `rejected_ids`). Required: `mainCitationString` / `parallelCitationString` / `caseName` / `accepted_ids` / `untagged_occurrences` / `ocr_corrected` / `ocr_note`. Null values allowed for ambiguous cases.
- Prompt: SCAN → GROUP → VERIFY workflow. Every tagged id MUST route to some `cited_case`. Ambiguous → solo case with null name. ~5K tokens, fictitious worked examples (Talbot v. Henning, Wendell v. Carson, Apex Corp. v. Northwood, etc.).
- Postprocess: validate per-call → two-pass merge (group_id name-aware, then canonical) → id-conflict resolution (Step 2c) → Phase 1 fallback (Step 2d) → build occurrences. Five audit logs (untagged, unrouted, id_conflicts, invalid, casename_warnings).
- 53 unit tests pass (5 skip locally for boto3-dependent batch helpers).

## Why this is left in place

The code is not the target pipeline going forward, but the experiment is preserved as the record of what was tried. The chunking quality analysis cites specific files and call outputs that would be unreadable without the source on disk. The audit logs (`data/grouped_*.jsonl`) document concrete failure cases worth referring back to when designing whatever pipeline comes next.

## Running

```bash
# Realtime smoke on specific clusters
python run_phase2.py --mode realtime --clusters 117935

# Re-run postprocess on cached emissions (no model calls)
python run_phase2.py --postprocess-only --clusters 117935

# Render HTML viewers
python render_grouped.py --clusters 117935
```
