# Experiment 0526 — Citator Pipeline Migration, Phase 1

> **Status note (2026-05-28):** The downstream pipeline this Phase 1 was built to feed was **abandoned** — see `../experiments_05192026/phased_eyecite_retrospective.md`. Phase 1 itself works (validated 383/383 structurally clean on the 0410 benchmark) and the code (`fetch_cl_data.py`, `assemble_tagged_text.py`, `render_tagged.py`) is reusable by any future pipeline that wants CL opinion text + tagged citation offsets. The rest of this README describes Phase 1 as implemented; the architecture it served is no longer the target.

Implements and validates **Phase 1** of the citator pipeline migration: retrieve each citing cluster's opinions from CourtListener and assemble per-opinion **tagged text** — plain text with `<cited>` tags marking every citation occurrence, ready for the downstream LLM phases (group → disposition → audit → classify).

The migration replaces the old "Haiku extracts citations and judges which section they're in" stage with "leverage CL's existing eyecite output to pre-tag citations, record exact char offsets, and let the LLM only group/classify." Full architecture and all 8 phases are specified in `../experiments_05192026/citator_pipeline_migration_plan.md`.

## What Phase 1 produces

One artifact per cluster at `data/tagged/{cluster_id}.json`:

```json
{
  "cluster_id": 12345,
  "opinions": [
    {"opinion_type": "020lead", "source_opinion_id": 6789, "tagged_text": "...<cited id=\"0\" group=\"g0\">Marbury v. Madison, 5 U.S. 137</cited>..."}
  ],
  "id_to_metadata_map": {
    "0": {"opinion_type": "020lead", "source_opinion_id": 6789, "offset": 412, "citation_string": "Marbury v. Madison, 5 U.S. 137", "group_id": "g0"}
  },
  "groups": {"g0": [0, 3, 5], "g1": [1]},
  "n_tags": 6, "n_unmatched_misses": 0, "empty_html": []
}
```

- **`id`** — per-occurrence, globally unique within the cluster, sequential `0,1,2,…` in reading order.
- **`group`** — per-cluster local handle (`g0,g1,…`) derived from CL's `data-id`. Occurrences CL's eyecite resolver chained to the same case (full + short + `Id.` + `supra`) share a group. A **hint** for Phase 2, not authority — no `cluster_id` is ever persisted.
- **`offset`** — char position of the tag's opening `<` in `tagged_text`. Downstream phases retrieve context windows from this same string.

## How it works

1. **`fetch_cl_data.py`** — runs inside the `cl-django` container. Per cluster, pulls every `Opinion` record (`id`, `type`, `html_with_citations`) and all `UnmatchedCitation` rows scoped to those opinions (`id`, `citing_opinion_id`, `citation_string`). Writes one `data/cl_fetch/{cluster_id}.json` per cluster. Only the fields Phase 1 needs are pulled.
2. **`../citator-pipeline/utils/assemble_tagged_text.py`** — the reusable core (BeautifulSoup + lxml, no DB). Per opinion:
   - **Tree walk** of `html_with_citations` builds plain text and records each `<span class="citation">`'s offset in a single pass — no string matching, so short cites (`Id.`, `supra`) get exact positions. All HTML is stripped except the `<cited>` tags; block tags → `\n\n`, `<br>` → `\n`.
   - **Additive safety net** — for `UnmatchedCitation` rows that drifted out of `html_with_citations`, `str.find()` the citation string and add it if it doesn't overlap an already-recorded span. Genuine misses (no match at all) are logged and skipped, never raised.
   - **Cluster enumeration** — opinions sorted into conventional reading order (`020lead` → `030/035concurrence` → `040dissent` → `010combined` → others); ids and groups assigned across all opinions; `<cited>` tags inserted at recorded offsets.
3. **`run_assemble.py`** — batch driver. Loops `data/cl_fetch/*.json`, writes tagged artifacts, runs a per-artifact **self-check** (every id's offset lands on its `<cited>` opening tag, `citation_string` follows verbatim, ids sequential `0..n_tags-1`), and writes `data/assemble_report.json` (machine-readable) + `data/assemble_issues.log` (problems + misses only).
4. **`test_assemble_synthetic.py`** — unit tests on hand-built HTML (tree walk, paragraph collapsing, safety-net overlap, reading-order sort, edge cases).

## Running

```bash
# 1. Fetch (in the courtlistener container)
docker cp data/cl_fetch_cluster_ids.csv cl-django:/opt/courtlistener/
docker cp fetch_cl_data.py cl-django:/opt/courtlistener/
docker exec cl-django python manage.py shell -c "exec(open('fetch_cl_data.py').read())"
docker cp cl-django:/opt/courtlistener/cl_fetch/ ./data/

# 2. Assemble + self-check (locally, ai-research env)
cd experiments_05262026 && python run_assemble.py
```

## Validation — full 0410 benchmark (383 citing clusters)

| Metric | Value |
|---|---|
| Clusters processed | 383 |
| **Self-check clean / with problems** | **383 / 0** |
| Total `<cited>` tags | 48,991 |
| Multi-opinion clusters | 119 |
| Clusters with blank-html skips | 0 |
| Unmatched-citation misses | 319 (across 76 clusters) |

Self-check verifies *structure*: 100% of recorded offsets land exactly on the right `<cited id group>` tag, `n_tags` equals the metadata-map size equals the actual tag count, and ids are contiguous.

### `source_opinion_id` is the unique opinion key

The first run flagged 2,656 `offset_not_at_tag` problems across 32 clusters — all clusters with **duplicate opinion types** (multiple concurrences/dissents share a `type`). Root cause: `opinion_type` was used as the key linking a citation back to its opinion's text. Fix: key on **`source_opinion_id`** (CL `Opinion.pk`), which is unique. `opinion_type` is kept too (needed for `section_role` derivation downstream). → 383/383 clean.

### Unmatched-citation misses are genuine garbage

The 319 misses are not recall gaps. They're (a) formatting variants of cites already tagged by the tree walk (e.g. `267 So.3d 89` already captured as `267 So.3d\n\n89`), or (b) eyecite mis-parses on **trial-court text** (`100trialcourt`), where left-margin line numbers bleed into citation strings — e.g. `18 Mar. 19`, `2012 WL 9`, `252 F. 7`. The safety net correctly declines all of them (no `str.find` match, because the real cite is already tagged). **Decision: leave Phase 1 faithful to source**; downstream LLM phases cope with the residual line-number noise; revisit only if Phase 7 validation shows measurable harm.

## Spot-check finding — duplicate `010combined` records (28% of clusters)

Manual spot-check of two real artifacts (a 1,368-tag SCOTUS cluster and a trial-court doc) confirmed structure beyond the synthetic tests, and surfaced a **data** issue the structural self-check can't catch:

**109 of 383 clusters (28%) carry both the split opinion records (lead/concurrence/dissent) AND a near-duplicate `010combined` record.** CL ingested the same opinions from two sources, so every citation in these clusters is tagged roughly twice. Investigation:

- Char sizes match (median combined/split ratio 1.02; no "stub split" cases).
- After normalizing OCR spacing (`U. S.` → `U.S.`), **103 of 109 are >70% pure duplicates** by citation string.
- The 6 that differ do so only by **parallel-reporter density** — combined interleaves `S.Ct.`/`L.Ed.` parallels (`311 U.S. 243, 61 S.Ct. 189, 85 L.Ed. 147` = one case) and case-caption/counsel boilerplate. These are extra *occurrences* and *reporter forms* of cases already in the splits, plus header metadata.
- Across all 109 clusters, exactly **one** combined-unique named case exists (`Terrell v Garcia, supra` — a short cite to a case already present in full).
- An 8-gram prose-shingle diff shows 8–24% of combined prose isn't in the splits, but reconstructing those passages shows it's caption boilerplate + parallel-citation runs, **not** footnotes or discussion. The analytical prose around each citation — what the treatment classifier's offset window reads — is identical between renderings.

**Conclusion:** dropping `010combined` when split records exist would lose zero cited cases and no treatment-relevant context. But because the migration is not cost-constrained and Phase 1's principle is to stay faithful to source, **Phase 1 keeps all records as-is.** The duplication is resolved downstream:

- **Phase 2** dedupes at the case level — the two-pass merge (by shared `group_id`, then by canonical `(mainCitationString, caseName)`) collapses a case cited in both the split-lead and combined into one row.
- **`section_role` and Phase 5 context windows prefer split records** (which carry `opinion_type`), using combined only as a fallback for a case found in no split record.
- **Phase 7** measures keep-vs-drop impact on treatment labels; if provably zero, combined can be dropped there as a clean optimization.

## Files

```
experiments_05262026/
├── fetch_cl_data.py            # CL fetch (runs in cl-django container)
├── run_assemble.py             # batch driver + self-check + disk report
├── test_assemble_synthetic.py  # unit tests on synthetic HTML
├── readme.md
└── data/                       # gitignored
    ├── cl_fetch_cluster_ids.csv
    ├── cl_fetch/{cluster_id}.json
    ├── tagged/{cluster_id}.json
    ├── assemble_report.json
    └── assemble_issues.log

../citator-pipeline/utils/assemble_tagged_text.py   # reusable Phase 1 core
```

## Status

Phase 1 done and validated (383/383 structurally clean). Phase 2 (`../experiments_05272026/`) was implemented but the overall phased eyecite architecture was abandoned after Phase 2 quality fell short — see `../experiments_05192026/phased_eyecite_retrospective.md`. The Phase 1 artifacts on disk and the assemble code remain valid building blocks for any future pipeline.
