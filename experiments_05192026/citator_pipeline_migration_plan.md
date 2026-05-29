# Citator Pipeline Migration Plan

> **Status: ABANDONED (2026-05-28).** The phased eyecite-anchored architecture described here was implemented through Phase 2 and then judged unviable. Phase 1 worked structurally (383/383 clean on 0410); Phase 2's LLM grouping step failed consistently on dense opinions across multiple prompt/schema/pagination iterations. See [`phased_eyecite_retrospective.md`](phased_eyecite_retrospective.md) for the verdict and [`../experiments_05272026/chunking_quality_analysis.md`](../experiments_05272026/chunking_quality_analysis.md) for the specific quality findings. The document below is preserved as the historical record of the design and decisions; Phases 3–8 were never implemented.

The citator pipeline is being rebuilt end-to-end around a new architecture: leverage CL's existing eyecite extraction to pre-tag citations as per-opinion artifacts, then run independent LLM pipelines on those artifacts to (a) group citations by case, (b) extract the lead opinion's disposition, and (c) classify treatments using offset-window context. An eyecite audit pass catches stage-1 recall gaps. This document covers all phases of the migration, from CL data fetch through demo rollout.

## Architecture overview

```
cluster_id (from benchmark or demo dataset)
   │
   ▼
Phase 1 — Retrieve and prepare opinion text
   per-cluster tagged JSON: opinions[{opinion_type, source_opinion_id, tagged_text}, ...] + id_to_metadata_map {id → opinion_type, source_opinion_id, offset, citation_string, group_id} + groups {group_id → [ids]}
   │
   ├──→ Phase 2 — Group citations by case (Haiku)        → grouped cases JSON
   ├──→ Phase 3 — Extract lead-opinion disposition (Sonnet) → disposition JSON
   └──→ Phase 4 — Audit citation recall (eyecite)        → audit report JSON
                    │
                    ▼
              Phase 5 — Classify treatments (Kimi)
              per-case treatment JSON
                    │
                    ▼
              Phase 6 — Orchestrate end-to-end
              per-cluster combined JSON
                    │
                    ▼
              Phase 7 — Validate
              eval metrics + spot-check reports
                    │
                    ▼
              Phase 8 — Deploy to demo dataset
              production data + doc updates
```

**Key invariants across phases:**
- `<cited id="N" group="gM">` tags carry two attributes:
  - `id` — per-occurrence, globally unique within the cluster (each `<cited>` tag has a distinct id; sequential 0, 1, 2, …)
  - `group` — per-cluster local handle indicating which spans CL's eyecite resolver chained to the same case. All occurrences of the same case (full + short + Id + supra) share the same `group`. Sequential g0, g1, g2, … per cluster.
  - The `group` attribute is a **hint, not authority**. The LLM in Phase 2 can confirm, split (move an id to a different group), or merge (declare two pre-existing groups are the same case).
- No `cluster_id` ever appears in tags, model input, or persisted metadata. CL's citation *grouping* (which spans share a `data-id`) is generally reliable, and that's what `group` captures.
- Citing-case `cluster_id` is preserved programmatically at the orchestrator level for joining phase outputs. Never sent to any LLM.
- Cited-case `cluster_id` is out of scope — cluster resolution for cited cases is not part of this pipeline.
- `match_status` (CL's matched/ambiguous/unmatched categorization) is not tracked. All citations become uniform `<cited>` tags regardless of CL's resolution outcome. Ambiguous and unmatched citations get their own per-occurrence group (no auto-grouping with siblings); the LLM merges them if it finds matches.
- Sections (the prior LLM-judged abstraction) are retired. Locations are char offsets within each opinion's `tagged_text`.
- `tagged_text` is plain text (all HTML stripped except `<cited>` tags). Newlines preserved for paragraph structure; no other HTML formatting carries through.
- Per-phase JSON outputs are persisted independently. Each phase reads from filesystem (small runs) or S3 (batch runs). All phases are replayable.

## Phases

Each phase is a coherent data transformation with a clear input/source/how/output contract. Sub-step refinement happens phase-by-phase; once a phase is fully refined, we implement and validate it before moving on.

---

### Phase 1 — Retrieve and prepare opinion text
**Status: DONE (committed `61d0477338`); validated 383/383 clean on the 0410 benchmark (48,991 tags).**
**Size: M (~2–3 days)**

**Input**: `cluster_id` (from benchmark CSV or demo dataset)
**Source**: CLReplica DB (`cl/search/Opinion`, `cl/citations/UnmatchedCitation`)

**How**:
1. **`fetch_cl_data.py`** — Python script that queries CLReplica via Django ORM (or raw SQL). Per cluster pulls:
   - **Opinion records**: `id` (needed to scope UnmatchedCitation rows), `type` (for reading-order sort), `html_with_citations` (the source text to parse).
   - **UnmatchedCitation rows**: `id` (for miss logging), `citing_opinion_id` (FK to scope rows to the right opinion), `citation_string` (the text to `str.find()` in the safety net). Parsed components (`volume`/`reporter`/`page`), `status`, `court_id`, `year`, `type` are NOT needed — the safety net matches on `citation_string` directly.
   - Persists raw fetch to `data/cl_fetch/{cluster_id}.json` for offline reuse.
2. **`assemble_tagged_text.py`** — reads the cl_fetch JSON. Per opinion:
   - Parse `html_with_citations` with BeautifulSoup (lxml parser).
   - **Walk the parse tree once** to build plain text AND record every citation span's offset in a single pass — no `str.find()` searching (which is unreliable for short cites like "Id." and "supra" that can match non-citation occurrences in prose and cannot disambiguate across different cases). For each tree node:
     - **`<span class="citation"…>`**: record `(offset = current plain-text length, inner_text = span.get_text(), data_id = span.get('data-id'))`. Append `inner_text` to the plain text. Covers ALL citation forms (full + short + Id + supra) and ALL match outcomes (matched + ambiguous + unmatched) uniformly. `data_id` is CL's grouping signal; `None` for `no-link` / `multiple-matches` spans.
     - **Paragraph-level tags** (`<p>`, `<div>`, `<h1>`–`<h6>`): recurse into children, then emit `\n\n` after to mark a paragraph break. Consecutive `\n\n` runs collapse to a single `\n\n` so nested or stacked blocks don't produce runaway separator chains.
     - **Line-break tags** (`<br>`): emit `\n` (single newline). `<br>` is a line break within a paragraph, not a paragraph break.
     - **Inline non-citation tags** (`<em>`, `<a>` outside citations, etc.): transparent — recurse into children without modifying the plain text.
     - **Text nodes** (NavigableString): append to plain text.
   - **Additive safety net** (UnmatchedCitation only): for citations CL recorded in the UnmatchedCitation table but didn't include in html_with_citations, fall back to `str.find()` matching. These rows carry full citation strings (volume/reporter/page parsed; e.g., "Smith v. Jones, 100 F.3d 200") — long enough that cursor-based string matching is reliable. For each `UnmatchedCitation` row scoped to this opinion (via `citing_opinion_id`):
     - Search for `citation_string` in the plain text with a moving cursor (advance past each match).
     - **Overlap check**: for a matched range `[m_start, m_end)` where `m_end = m_start + len(citation_string)`, compare against every citation already recorded in this opinion's tree walk (each occupying range `[r.offset, r.offset + len(r.inner_text))`). Standard interval-overlap test: `m_start < r_end AND m_end > r_start`.
       - On match AND **no overlap** with any recorded range → add `(offset, citation_string, data_id=None)` to the citation list. *Catches drift*: e.g., UnmatchedCitation has "Brown v. Board, 347 U.S. 483" but html_with_citations didn't wrap it; safety net adds it at the offset where `str.find()` located it.
       - On match AND **overlap** with a recorded range → skip (citation already captured by tree walk; adding would produce duplicate / nested `<cited>` tags at the same span).
     - On miss (no `str.find()` hit) → record in the artifact's `unmatched_miss_details` and skip.
     - (Most UnmatchedCitation rows already appear in html_with_citations as `<span class="citation no-link">` and are captured in the tree walk — this step exists for the drift cases.)
3. Cluster-level enumeration + tag insertion (operates on the per-opinion plain text + recorded citation list from step 2 — no `<cited>` tags exist in the text yet; the recorded list carries the offset/citation_string/data_id for each citation):
   - List opinions in conventional reading order: `020lead`/`025plurality` → `030concurrence`/`035concurrenceinpart` → `040dissent` → `010combined` → others sorted by type string.
   - **Group assignment**: iterate the recorded citation list across opinions in reading order. Bucket by `data_id`:
     - All recorded citations sharing the same `data_id` → one group
     - Citations with `data_id=None` (unmatched + ambiguous + UnmatchedCitation safety-net matches) → each gets its own solo group
     - Assign sequential `group_id = g0, g1, g2, …` in first-encounter order.
   - **Per-occurrence id assignment**: iterate the recorded citation list in reading order; assign sequential `id = 0, 1, 2, …` to each entry.
   - **Tag insertion**: for each opinion, build the final `tagged_text` by walking the plain text from step 2 and inserting `<cited id="N" group="gM">…</cited>` tags around each recorded citation at its recorded offset. Track the position of the opening `<` in the final string as each tag is inserted — that's the `offset` value stored in `id_to_metadata_map`. `data_id` is dropped from the persisted artifact (used only as the grouping key during this step).
4. Build `id_to_metadata_map` and `groups` map (see Output schema below). Tally `n_tags` (total `<cited>` tags across all opinions = size of `id_to_metadata_map`) and `n_unmatched_misses` (UnmatchedCitation rows that failed the safety-net `str.find()`); each miss's details land in `unmatched_miss_details`. Populate `empty_html` with the `opinion_type`s of any records skipped for blank `html_with_citations`. The batch driver (`run_assemble.py`) writes `data/assemble_report.json` (machine-readable per-cluster summary + problems + miss details) and `data/assemble_issues.log` (human-readable, problems + misses only).
5. **Edge cases**:
   - Blank `html_with_citations` on an Opinion record → skip that record (don't add it to `opinions[]`); append its `opinion_type` to the artifact's `empty_html` list. No fallback to plain_text. The cluster's other opinions still process normally.
   - All records blank → every type lands in `empty_html`, `opinions[]` is empty, `n_tags` is 0. Just the degenerate case of the above; no separate handling.
   - **Duplicate `010combined` records are kept, not dropped.** When a cluster has both split records and a near-duplicate `010combined` re-render (28% of the 0410 benchmark), Phase 1 stays faithful to source and tags all of them. Deduplication is a Phase 2 concern (case-level two-pass merge); the keep-vs-drop tradeoff is measured in Phase 7. See the 2026-05-26 decision.

**Output schema** at `data/tagged/{cluster_id}.json`:
```json
{
  "cluster_id": 12345,
  "opinions": [
    {"opinion_type": "020lead",    "source_opinion_id": 6789, "tagged_text": "...<cited id=\"0\" group=\"g0\">Marbury v. Madison, 5 U.S. 137</cited>...<cited id=\"1\" group=\"g1\">Erie R.R. Co. v. Tompkins</cited>...<cited id=\"3\" group=\"g0\">Marbury, 5 U.S. at 145</cited>...<cited id=\"5\" group=\"g0\">Id.</cited>..."},
    {"opinion_type": "040dissent", "source_opinion_id": 6790, "tagged_text": "...<cited id=\"6\" group=\"g0\">Marbury, supra</cited>...<cited id=\"8\" group=\"g2\">Smith v. Jones</cited>..."}
  ],
  "id_to_metadata_map": {
    "0": {"opinion_type": "020lead",    "source_opinion_id": 6789, "offset": 412,  "citation_string": "Marbury v. Madison, 5 U.S. 137", "group_id": "g0"},
    "1": {"opinion_type": "020lead",    "source_opinion_id": 6789, "offset": 1523, "citation_string": "Erie R.R. Co. v. Tompkins",      "group_id": "g1"},
    "3": {"opinion_type": "020lead",    "source_opinion_id": 6789, "offset": 5234, "citation_string": "Marbury, 5 U.S. at 145",          "group_id": "g0"},
    "5": {"opinion_type": "020lead",    "source_opinion_id": 6789, "offset": 7890, "citation_string": "Id.",                              "group_id": "g0"},
    "6": {"opinion_type": "040dissent", "source_opinion_id": 6790, "offset": 145,  "citation_string": "Marbury, supra",                   "group_id": "g0"},
    "8": {"opinion_type": "040dissent", "source_opinion_id": 6790, "offset": 1024, "citation_string": "Smith v. Jones",                   "group_id": "g2"}
  },
  "groups": {
    "g0": [0, 3, 5, 6],
    "g1": [1],
    "g2": [8]
  },
  "n_tags": 6,
  "n_unmatched_misses": 0,
  "empty_html": []
}
```

(IDs are non-contiguous in this example because some ids — 2, 4, 7 — sit in opinions / locations not shown; in a real artifact they'd be sequential.)

`empty_html` lists the `opinion_type`s of any Opinion records skipped because `html_with_citations` was blank. `[]` here means nothing skipped. A partial skip looks like `"empty_html": ["030concurrence"]` (concurrence skipped, others processed); an all-blank cluster has every type listed and `opinions: []`, `n_tags: 0`.

**Encoding notes**:
- NBSPs and unicode preserved as-is in `tagged_text`. NBSP-driven UnmatchedCitation misses surface in `unmatched_misses.log`, not as Phase 1 bugs.
- `<cited id="N" group="gM">` is plain ASCII; safe inside arbitrary unicode strings.
- JSON serialization escapes quotes; cost is ~4 chars/tag for the two attributes, negligible.

---

### Phase 2 — Group citations by case
**Status: spec refined, implementation pending**
**Size: M (~2–3 days)**

**Input**: `data/tagged/{cluster_id}.json` (Phase 1 artifact)
**Source**: local filesystem (small runs) or S3 (batch runs)

**How**:
1. **`paginate_and_call.py`** — char-based token estimator measures each opinion's `tagged_text`. **One opinion per call** (chunks of long opinions become dedicated calls). Rules: (a) every opinion produces at least one call; no cross-opinion packing; (b) an opinion at or under `MAX_OPINION_TOKENS` (20K) becomes one whole-opinion call; (c) an opinion above 20K is chunked into overlapping 20K pieces (5K overlap), each its own dedicated call; (d) `010combined` and non-combined are treated identically under this rule — the combined-isolation guarantee is automatic. Rationale: small per-call payloads keep attention focused; cross-opinion packing reintroduces attention dilution.
2. **AWS Bedrock call to Haiku 4.5** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) — batch for large runs, on-demand (Converse API) for small runs. Tool spec in `bedrock_converse_utils.py` enforces output schema. **Prompt is a fresh write** — not a port of `haiku_extractor`. The model's task, operating ONLY on what it can see in this call's chunks:
   - **Confirm** Phase 1's pre-grouping: for each `group=gM` it sees, is it one case? Provide canonical `mainCitationString` + `caseName`.
   - **Split** if a pre-grouping is wrong: identify which ids should be moved to a different group (e.g., "id=7 in g0 is actually Erie, not Marbury — put it in its own group").
   - **Merge** if two pre-existing groups visible in this call are actually the same case (common for unmatched/ambiguous cites that landed in separate solo groups).
   - **Add** untagged occurrences (citations CL missed) — provide verbatim snippet from source, character-for-character.
   - The model does NOT need to reason about ids it can't see. Cross-chunk merging happens in postprocess, not in the model.
3. **`postprocess_offsets.py`** — merges per-call outputs. Two-pass merge:
   - **Pass 1: group_id merge.** Cited cases from different calls whose `accepted_ids` share a `group_id` (looked up in `id_to_metadata_map[N].group_id`) get merged. This catches the multi-chunk case where chunk 1 emits the full cite for g0 and chunk 2 emits only the "Id." reference for g0 — they merge into one case row.
   - **Pass 2: canonical name merge.** Cases sharing `(mainCitationString, caseName)` get merged. This catches the case where the model split or merged groups across chunks differently from CL's pre-grouping.
   - **Duplicate `010combined` records:** 28% of clusters carry both split opinion records (lead/concurrence/dissent) AND a near-duplicate `010combined` re-render (same opinions from a second CL source). A case cited in both lands in the same `group_id` when matched (Pass 1 merges it) or under the same canonical name when unmatched (Pass 2 merges it), so the two passes collapse the duplication into one case row without special-casing. See the 2026-05-26 decision below.
   - Resolves `accepted_ids` → `(opinion_type, offset)` via `id_to_metadata_map`. Resolves each `untagged_occurrence`'s `opinion_handle` to its `(source_opinion_id, opinion_type)` via the call's input record, then validates the snippet via `str.find()` within that opinion's `tagged_text` (hard drop on no match). Each occurrence keeps its `opinion_type` — Phase 2 does **not** derive a single `section_role`; that's set at the Phase 5/6 most-negative collapse as the role of the winning occurrence.
   - **Merge conflict resolution (when two emissions of the same merged case disagree):**
     - `mainCitationString` / `caseName`: longer / more-specific string wins (the model that saw more context produced the more complete canonical form). Loser logged.
     - `ocr_corrected` / `ocr_note`: OR-combine. If any emission says `true`, the merged row is `true`; notes concatenated with `; `.
     - Untagged-occurrence overlap dedup: when chunks 1 and 2 overlap and both emit the same untagged occurrence (same `source_opinion_id` + snippet), keep the first accepted, drop the duplicate.
   - **Merge conflict on a single id (Step 2c — added 2026-05-28):** after the two-pass merge, scan for ids that ended up in more than one final cited_case. This happens when adjacent chunks (overlap region) disagree about which case a tagged id refers to, and Pass 1/2 don't merge those cases (genuinely different canonical names). Winner rule: case with the most occurrences (accepted_ids + untagged); tie-break on longest `mainCitationString`; final tie-break on case index. Loser cases have the id stripped; a loser with zero accepted_ids and zero untagged is dropped entirely. Each conflict logged to `data/grouped_id_conflicts.jsonl`.
   - **Unrouted-id fallback (Step 2d — added 2026-05-28):** every Phase 1 id must end up in some `cited_case`. If the model omitted an id, postprocess attaches it to the merged case sharing its `group_id` (Phase 1's grouping is authoritative for the cited case); if no sibling exists in any merged case, postprocess creates a solo `cited_case` with null `mainCitationString` / `caseName` and the omitted id as its only `accepted_id`. Sibling pick rule: when multiple merged cases touch the same group_id (chimera-fix kept them split), prefer the case with the most occurrences. Each restoration logged to `grouped_unrouted_ids.jsonl` with `fallback: "group_id_sibling"` or `"solo_from_phase1"`.
   - **Audit logs (review-driven debugging — one JSON per line):**
     - `data/grouped_untagged_log.jsonl` — every untagged occurrence, accepted and dropped (`cluster_id`, `case`, `opinion_handle` + resolved `source_opinion_id` / `opinion_type`, `snippet`, `outcome: accepted|dropped`, `drop_reason` if dropped, `offset` if accepted). Tunes the prompt's verbatim instruction from real failures.
     - `data/grouped_unrouted_ids.jsonl` — every Phase 1 id the model omitted, with the fallback strategy applied (`fallback: "group_id_sibling"` if attached to an existing case, `"solo_from_phase1"` if a solo case was created). Non-zero counts indicate prompt-compliance gaps but no longer mean lost data.
     - `data/grouped_invalid_ids.jsonl` — fabricated `accepted_ids` (id not in `id_to_metadata_map`). The id is dropped from the emission; the case row survives. Logs the offending id and the model's output context.
     - `data/grouped_casename_warnings.jsonl` — `caseName` double-check failures: (a) `caseName` is not a substring of `mainCitationString` after normalization, or (b) two merged emissions disagree on `caseName` after normalization. Non-blocking; surfaces model self-consistency issues for review.
     - `data/grouped_id_conflicts.jsonl` — one entry per resolved id conflict (`cluster_id`, `id`, `citation_string`, `group_id`, `winner` {mainCit, caseName, n_occurrences}, `losers` [...]). Surfaces overlap-region disagreements; review to validate the winner rule is picking right.

**LLM input JSON** (per call; `cluster_id` intentionally absent — model doesn't need it):
```json
{
  "opinions": [
    {"opinion_handle": 0, "opinion_type": "020lead",    "chunk_index": 1, "total_chunks": 1, "tagged_text": "..."},
    {"opinion_handle": 1, "opinion_type": "040dissent", "chunk_index": 1, "total_chunks": 1, "tagged_text": "..."}
  ]
}
```
- `opinion_handle` is a per-call local handle (small int `0,1,2,…`). The model echoes it back in `untagged_occurrences` to disambiguate which opinion (or chunk thereof) it found the untagged citation in — important because 32 of 383 clusters in the 0410 benchmark have multiple opinions of the same `opinion_type`. Postprocess maps `opinion_handle` → `(source_opinion_id, opinion_type)` from the call's input record.
- `chunk_index` and `total_chunks` always present (whole opinions are `1`/`1`). Multi-chunk signal lets the model soften output when it sees a partial picture (e.g., leave `mainCitationString` empty for a short cite whose full form may be in another chunk; be cautious about declaring a citation "missing"). Postprocess does the cross-chunk merge mechanically either way.
- The `<cited id="N" group="gM">` tags inline in `tagged_text` give the model the grouping signal it needs — only for ids visible in this call. No cluster-wide groups summary; cross-chunk merging is postprocess's job.

**LLM output JSON** (per call; rejected_ids dropped 2026-05-28 second-pass after eyecite precision trusted):
```json
{
  "cited_cases": [
    {
      "mainCitationString": "Talbot v. Henning, 500 U.S. 412 (1991)",
      "parallelCitationString": "111 S.Ct. 2050, 114 L.Ed.2d 540",
      "caseName": "Talbot v. Henning",
      "accepted_ids": [0, 3, 5],
      "untagged_occurrences": [
        {"opinion_handle": 0, "snippet": "verbatim text from source, char-for-char"}
      ],
      "ocr_corrected": false,
      "ocr_note": null
    }
  ]
}
```
- One object per distinct cited case the model identifies. Confirm / split / merge are mechanical, not signaled: confirm = `accepted_ids` is exactly a Phase-1 group's ids; split = two cited_cases partition a Phase-1 group's ids; merge = `accepted_ids` is the union of two Phase-1 groups' ids. Postprocess infers grouping changes by comparing `accepted_ids` against `id_to_metadata_map[…].group_id`.
- `mainCitationString` is the canonical full citation: case name + PRIMARY reporter + year in parentheses. Null when the case is referenced solely by short cite / Id. / supra and no full form appears in this chunk (postprocess can chain back via shared group_id or canonical name from another chunk).
- `parallelCitationString` is a comma-separated list of parallel reporter cites only (no name, no year), e.g., `"81 S.Ct. 1101, 6 L.Ed.2d 393"`. Null when the case has no parallel reporters.
- `caseName` is emitted as a separate field even though it duplicates the prefix of `mainCitationString` — postprocess uses it as a consistency check (`caseName` should be a normalized substring of `mainCitationString`; mismatches go to `data/grouped_casename_warnings.jsonl`).
- **No rejection.** Every tagged id in the chunks visible to a call MUST appear in some `cited_case`'s `accepted_ids`. When the model cannot identify what case an id refers to (orphaned `Id.`, unfamiliar reporter, partial-chunk visibility), it emits a `cited_case` with null `mainCitationString` and null `caseName` — postprocess can resolve later via group_id or canonical merge. Silent omission is a bug, logged to `data/grouped_unrouted_ids.jsonl`.
- `untagged_occurrences[].snippet` must be verbatim from the source — hard-drop on `str.find()` miss in the referenced opinion's `tagged_text`. Postprocess logs all accepted + dropped untagged occurrences (see audit logs under postprocess).
- OCR fields are case-level (not per-occurrence): the model judges OCR correction once per case.

**Pagination parameters**:
| Parameter | Default |
|---|---|
| Max opinion tokens per call (chunk threshold AND chunk size) | 20,000 tokens (~74K chars). Single knob: an opinion over this is chunked into pieces of this size. Set deliberately small to keep attention focused; smoke runs at 150K showed Haiku 4.5 silently dropping ids in dense opinions held in single large chunks. |
| Chunk overlap | 5,000 tokens |
| Single-opinion-per-call | Yes. No cross-opinion packing. Every call sees exactly one opinion (or one chunk thereof). Combined-vs-non-combined isolation is automatic. |

Token estimation is char-based — chars/3.7, no tokenizer dependency (legal text ≈ 3.5–3.8 chars/token).

Example: a cluster with 1 lead (~35K), 1 concurrence (~5K), 1 dissent (~50K), and 1 combined (~90K). The lead chunks into 2 calls; the concurrence is one call; the dissent chunks into 3 calls; the combined chunks into 5 calls. Total = 11 calls (vs 2 under the prior FFD-packing model). Smaller per-call payloads, more calls, postprocess merges across them via shared `group_id` and canonical name.

**Chunk-boundary safety:** chunks must never split inside a `<cited id="N" group="gM">…</cited>` tag (malformed input + breaks the model's grouping signal). At each nominal cut, walk to the nearest paragraph break (`\n\n`) that isn't inside an open `<cited>` tag, within ±1K chars; fall back to a sentence break, then to any safe char position outside any tag. The 5K-token overlap region observes the same rule on both edges. `opinion_handle` is always `0` under the one-opinion-per-call rule, but kept in the output schema for compatibility with the model contract.

**Output schema** at `data/grouped/{cluster_id}.json` (per cited case):
| Field | Source | Notes |
|---|---|---|
| `cluster_id` | orchestrator | citing cluster id |
| `mainCitationString` | LLM | canonical full citation: case name + primary reporter + `(year)` |
| `parallelCitationString` | LLM | comma-separated parallel reporter cites only; null if none |
| `caseName` | LLM | repeated from `mainCitationString` prefix; consistency-checked by postprocess |
| `occurrences` | post-process | list of `{opinion_type, source_opinion_id, offset, citation_string, source: "tagged"\|"untagged"}`. Each occurrence keeps its `opinion_type` — no single `section_role` derived here; that's a Phase 5/6 field. |
| `ocr_corrected` | LLM | bool |
| `ocr_note` | LLM | nullable explanation |

---

### Phase 3 — Extract lead-opinion disposition
**Status: grand-plan only; sub-step refinement pending**
**Size: S–M (~1–2 days)**

**Input**: `data/tagged/{cluster_id}.json` (Phase 1 artifact)
**Source**: local filesystem or S3

**How**:
1. **`identify_lead_opinion.py`** (utility) — selects the lead opinion artifact: `020lead`/`025plurality`/`015unamimous` if present, else regex within `010combined` for the first dissent/concurrence header to compute `lead_range`, else fall back to the first opinion in listing order.
2. **AWS Bedrock call to Sonnet 4.6** — one call per cluster. Input: head (first ~2K tokens) + tail (last ~2K tokens) of `tagged_text[lead_range]`, delimited (`=== HEAD ===` / `=== TAIL ===`). Tool spec enforces output schema. Prompt is a fresh write.
3. **Postprocess** (in `extract_disposition.py`) — reconciles head vs tail (prefer tail on disagreement); logs agreement signal.

**Output schema** at `data/disposition/{cluster_id}.json` (one row per cluster):
| Field | Source |
|---|---|
| `cluster_id` | orchestrator |
| `disposition_treatment` | LLM |
| `disposition_quote` | LLM |
| `disposition_notes` | LLM |
| `disposition_head_vs_tail_agreement` | post-process | `agree` / `disagree_preferred_tail` / `head_only` / `tail_only` |

---

### Phase 4 — Audit citation recall
**Status: grand-plan only; sub-step refinement pending**
**Size: S (~1 day)**

**Input**: `data/tagged/{cluster_id}.json` (Phase 1) + `data/grouped/{cluster_id}.json` (Phase 2)
**Source**: local filesystem or S3

**How**:
1. **`eyecite_audit.py`** — for each opinion: strip all `<cited …>` and `</cited>` tags (regardless of `id`/`group` attribute values) to recover raw text.
2. Run eyecite (`get_citations` + `resolve_citations`) on each opinion independently. Library pinned via `uv`.
3. Canonical-case-match diff: compare eyecite-found cites against Phase 2 grouped cases (matched by normalized citation string + case name). Emit missed-by-Phase2 and extra-from-Phase2 per opinion.

**Output schema** at `data/audit/{cluster_id}.json`:
```json
{
  "cluster_id": 12345,
  "per_opinion": [
    {"opinion_type": "020lead", "eyecite_n": 47, "phase2_n": 45, "missed": [...], "extra": [...]}
  ],
  "cluster_summary": {"eyecite_n": ..., "phase2_n": ..., "recall_gap_pct": ...}
}
```

Advisory only — does not gate downstream output.

---

### Phase 5 — Classify treatments
**Status: grand-plan only; sub-step refinement pending**
**Size: M (~2–3 days)**

**Input**: `data/grouped/{cluster_id}.json` (Phase 2) + `data/tagged/{cluster_id}.json` (Phase 1, for offset-window retrieval)
**Source**: local filesystem or S3

**How**:
1. **`window_context.py`** (helper) — per case, build context windows: for each occurrence, slice `tagged_text` from the owning opinion at `[offset - 800, offset + 1200]`; union overlapping windows for multi-occurrence within the same opinion.
2. **AWS Bedrock call to Kimi K2.5** (`moonshotai.kimi-k2.5`) — batch or on-demand. **Prompt is a fresh write** — not a port of `kimi_classifier` — designed for the new offset-window context format and the current 22-entry taxonomy. Schema inlined in user prompt (Kimi doesn't support tool_use).
3. **Postprocess** — canonicalize treatments against `TREATMENT_RANK`, derive `severity` + `direction` via `severity_mapping` / `direction_mapping` (existing `postprocess.py`; minimal changes).

**Output schema** at `data/classified/{cluster_id}.json` (one row per cited case, most-negative-wins):
| Field | Source |
|---|---|
| `cluster_id` | orchestrator |
| `mainCitationString` | from Phase 2 |
| `caseName` | from Phase 2 |
| `treatment` | LLM |
| `severity` | derived from treatment |
| `direction` | derived from treatment |
| `supporting_quote` | LLM |
| `section_role` | derived at collapse: `opinion_type` of the winning (most-negative) occurrence, mapped to `lead`/`concurrence`/`dissent`/etc., with split records preferred over `010combined` |
| `ocr_corrected` | from Phase 2 |
| `ocr_note` | from Phase 2 |

---

### Phase 6 — Orchestrate end-to-end
**Status: grand-plan only; sub-step refinement pending**
**Size: M (~2–3 days)**

**Input**: List of `cluster_id`s
**Source**: benchmark CSV (e.g., `0410.csv`) or demo-dataset CSV

**How**:
1. **`run_pipeline.py`** (new) — orchestrator. Per cluster: Phase 1 → [Phase 2 ∥ Phase 3 ∥ Phase 4 in parallel] → Phase 5 (depends on Phase 2). Aggregates outputs into a single final-per-cluster JSON.
2. **`run_batch.py`** (updated) — Bedrock batch path. Submit/collect mirrors the existing pipeline's structure but outputs are JSON per cluster rather than monolithic CSV. JSON-first means: easier to load partial results, easier to diff between runs, no row-shape contortions when a cluster has both per-citation rows (Phase 5) and per-cluster fields (Phase 3).
3. **Old pipeline** preserved behind a `--legacy` flag until Phase 7 validation passes.

**Output**: `data/output/{cluster_id}.json` per cluster combining Phase 2 cited cases + Phase 3 disposition + Phase 4 audit summary + Phase 5 treatments. Aggregate `data/output/manifest.json` lists all clusters processed in this run with run metadata.

---

### Phase 7 — Validate
**Status: grand-plan only; sub-step refinement pending**
**Size: S–M (~1–2 days)**

**Input**: Phase 6 outputs + benchmark labels (`data/benchmark_original/0410.csv`)
**Source**: local filesystem

**How**:
- Eval notebook reads the new JSON-per-cluster output (departure from the existing CSV path; loader code needs updating).
- Per-treatment / per-severity / per-direction P/R/F1, Cohen's κ, Gwet's AC1, QWK, treatment-rank-distance distribution — same metrics as `experiments_05182026/eval_benchmark.ipynb`.
- Spot-check problem clusters: 1722, 4450553, the 5 Haiku-dropped SCOTUS opinions, low-Jaccard state cases.
- Apples-to-apples comparison vs 0407 (9-opinion) and 0518 (383-opinion) baselines.
- Disposition: manual spot-check of 20 opinions vs expert labels.
- **Duplicate `010combined` keep-vs-drop measurement.** Re-run the 109 affected clusters (28% of the benchmark) with `010combined` records excluded and diff treatment labels against the keep-combined run. If excluding combined changes zero labels (expected — the prefer-splits rule already de-prioritizes combined occurrences), drop combined in Phase 1 as a clean optimization (~2× less Phase 2/5 compute on those clusters). If it changes labels, keep combined and investigate which cases flipped.

**Output**: `experiments_05192026/data/eval/` — eval CSVs + notebook outputs + comparison.md update.

---

### Phase 8 — Deploy to demo dataset
**Status: grand-plan only; sub-step refinement pending**
**Size: S–M (~1–2 days)**

**Input**: 4,125-cluster demo dataset (cluster_id list)
**Source**: `experiments_05012026/data/` (demo dataset scoping outputs)

**How**:
- Run Phase 6 at scale via Bedrock batch (Haiku + Sonnet + Kimi). Estimated $200–250 batch.
- Output to S3, then ingested into citator-demo-site backing store.
- Update demo-site code if Phase 6 JSON schema diverges from current consumer contract.
- Update `CLAUDE.md`, update relevant memory files.

**Output**: production dataset in citator-demo-site backing store + updated docs.

---

**Total estimated effort: ~14–18 working days.** Phase 1 is the critical predecessor; Phases 2/3/4 are independent of each other once Phase 1 lands.

## Validation criteria

- **Hallucination guard** (Phase 2): 0 emitted rows without a provable source location, except `ocr_corrected=true` rows (which still have an `accepted_id` anchor).
- **Offset correctness** (Phase 2): 100% of spot-checked (citation, opinion, offset) tuples grep-verifiable.
- **No silent opinion drops** (Phase 1+2): 383/383 opinions produce non-empty extractions on the 0410 benchmark (vs Haiku 0518's 378/383).
- **F1 against expert labels** (Phase 5): ≥ Haiku 0518 baseline on the 0410 benchmark.
- **Disposition accuracy** (Phase 3): ≥ 90% on spot-checked sample.
- **Audit recall gap** (Phase 4): < 5% on the 383-opinion benchmark.

## Key risks

| Risk | Mitigation |
|---|---|
| UnmatchedCitation rows don't string-match (line-wrap, NBSP) | Skip with warning; LLM may rediscover via untagged_occurrences |
| LLM paraphrases untagged_occurrence snippets | Hard drop on grep failure |
| Same `<cited id="N" group="gM">` near a chunk boundary gets grouped with different cases in two overlapping chunks | Postprocess two-pass merge: first by shared `group_id` from `id_to_metadata_map`, then by canonical `(mainCitationString, caseName)` |
| Lead opinion regex misses a dissent/concurrence header in 010combined-only clusters | Catch in Phase 7 spot-check; expand patterns |
| Bedrock batch 100-record minimum unmet for small runs | Use real-time inference for small runs; pad with synthetic records for batch |
| Phase 6 JSON output diverges from eval notebook's expected CSV shape | Phase 7 updates eval-notebook loader; both paths land in same run |
| Phase 5 offset window too small for context | Tune defaults in Phase 7 |

## Files affected

```
citator-pipeline/
├── utils/
│   ├── assemble_tagged_text.py    (NEW — Phase 1)
│   ├── identify_lead_opinion.py   (NEW — Phase 3 utility)
│   ├── paginate_and_call.py       (NEW — Phase 2)
│   ├── extract_disposition.py     (NEW — Phase 3)
│   ├── postprocess_offsets.py     (NEW — Phase 2 postprocess)
│   ├── eyecite_audit.py           (NEW — Phase 4)
│   ├── window_context.py          (NEW — Phase 5 helper)
│   ├── instructions.py            (new prompts: citation_grouping, disposition_extractor, treatment_classifier; haiku_extractor + kimi_classifier retired)
│   ├── bedrock_converse_utils.py  (new tool specs)
│   ├── postprocess.py             (treatment canonicalization + severity/direction; minimal changes)
│   └── eval_utils.py              (loader updates for JSON-per-cluster output)
├── run_pipeline.py                (NEW — Phase 6 orchestrator)
└── run_batch.py                   (updated — JSON-per-cluster outputs)

experiments_05262026/                  (Phase 1 experiment — DONE)
├── data/
│   ├── cl_fetch/{cluster_id}.json     (Phase 1 input — raw CL fetch)
│   ├── tagged/{cluster_id}.json       (Phase 1 output — tagged artifact)
│   ├── assemble_report.json           (Phase 1 per-cluster summary + problems + miss details)
│   └── assemble_issues.log            (Phase 1 human-readable problems + misses)
├── fetch_cl_data.py                   (CLReplica fetcher; runs in cl-django container)
├── run_assemble.py                    (Phase 1 batch driver + self-check)
└── test_assemble_synthetic.py         (Phase 1 unit tests)

experiments_05192026/                  (Phase 2+ experiment — pending)
├── data/
│   ├── grouped/{cluster_id}.json      (Phase 2 output)
│   ├── disposition/{cluster_id}.json  (Phase 3 output)
│   ├── audit/{cluster_id}.json        (Phase 4 output)
│   ├── classified/{cluster_id}.json   (Phase 5 output)
│   ├── output/{cluster_id}.json       (Phase 6 combined output)
│   ├── eval/                          (Phase 7 eval artifacts)
│   ├── grouped_untagged_log.jsonl       (Phase 2 audit log — accepted+dropped untagged)
│   ├── grouped_rejected_ids.jsonl       (Phase 2 audit log — implicitly-rejected ids)
│   ├── grouped_invalid_ids.jsonl        (Phase 2 audit log — fabricated accepted_ids)
│   └── grouped_casename_warnings.jsonl  (Phase 2 audit log — caseName double-check)
├── run_eyecite.py                     (0519 comparison; deprecated post-Phase 4)
└── comparison.ipynb                   (0519 comparison + Phase 7 validation cells)
```

## Future ideas (next iteration; not in current scope)

### Opinion summaries to enrich treatment classification context
Phase 5 currently sees only an offset window around each citation. A future iteration could enrich Phase 5 with cluster-level context:

1. Have Phase 2 (Haiku citation grouping) emit a summary per chunk / per opinion type in addition to grouped citations — e.g., `summary_lead_chunk1`, `summary_lead_chunk2`, `summary_concurrence`, `summary_dissent`.
2. A separate aggregation step produces a whole-cluster summary by combining per-chunk summaries.
3. Phase 5 receives both the offset-window context AND the cluster summary, giving the classifier more context for treatment classification.

**Motivation**: especially helpful for citations where the local window is ambiguous about how the citing court treated the cited case (the disposition of the citing case can shape how a cited case is treated). Defer until the current pipeline lands and we have a baseline to measure summary-enrichment against.

## Open Questions

- **Phase 3 — Disposition: one Sonnet call or two?** Concat'd head + tail in one call is cheaper and lets the model reconcile internally. Two separate calls give cleaner head-vs-tail comparison. Default plan: one call, measure agreement, escalate to two if needed.
- **Phase 8 — Persistence layout for the 4,125-opinion run**: local per-cluster JSON is fine for the benchmark. For the demo run this becomes S3. Phase 8 decision.

## Decisions log

- **2026-05-21** — Disposition split from citation grouping into its own Sonnet pipeline. Citation grouping reverts to Haiku.
- **2026-05-21** — `cluster_id` dropped from `<cited>` tags, model input, and the internal metadata map.
- **2026-05-21** — Char offsets index into stripped-markup tagged_text. Single canonical artifact; Phase 5 retrieves windows from the same string.
- **2026-05-22** — Per-opinion artifacts replace pre-concatenation. Eliminates `opinion_ranges` and char-range arithmetic across concatenated text.
- **2026-05-22** — LLM input format is JSON. `opinions` array carries `opinion_type` + `chunk_index` + `total_chunks` per entry. One call per cluster by default; bin-pack into multiple calls only when total exceeds budget.
- **2026-05-22** — Cited IDs are global across the cluster, sequential in conventional opinion listing order (lead → concurrence → dissent → combined → others), document order within each opinion.
- **2026-05-22** — Blank `html_with_citations`: skip the record gracefully; no fallback to plain_text. Process the cluster's other opinions normally. Skipped records' `opinion_type`s are listed in the artifact's `empty_html` field (a single field that unifies partial-skip and all-blank cases — replaces an earlier per-case `reason` flag). All-blank cluster = every type in `empty_html`, `opinions: []`, `n_tags: 0`.
- **2026-05-22** — Phase 5 reframed from "Stage 2 retrofit" to a fresh prompt rewrite. Both Phase 2 (Haiku) and Phase 5 (Kimi) get fresh prompts; existing `haiku_extractor` + `kimi_classifier` prompts retired.
- **2026-05-22** — Phase 6 output is JSON-per-cluster, not CSV. Eval notebook (Phase 7) loader updated.
- **2026-05-22** — Citing-case `cluster_id` preserved programmatically at the orchestrator level; never sent to any LLM. Cited-case `cluster_id` linking is out of scope.
- **2026-05-22** — Opinion-summary enrichment captured as a future-iteration idea; not in current scope.
- **2026-05-22** — Plan doc reframed to cover the full pipeline (not just stage 1). Cross-cutting reference sections (output schemas, LLM input format, pagination parameters) folded into their owning phase blocks.
- **2026-05-22** — `match_status` dropped from `id_to_metadata_map` and from Phase 2 `occurrences`. CL's matched/unmatched/ambiguous categorization isn't used by any pipeline phase; all citations become uniform `<cited id="N">` tags.
- **2026-05-26** — `source_opinion_id` (CL Opinion.pk) is carried on `opinions[]` entries AND `id_to_metadata_map` as the unique per-opinion key. (Briefly dropped 2026-05-22 as "unconsumed" — wrong: `opinion_type` is NOT unique within a cluster, so the id→opinion link collapsed for the 32 dup-opinion-type clusters in the 383 benchmark, producing 2,656 wrong-opinion offset lookups. Re-added → 383/383 clean. `opinion_type` is kept too, for `section_role`.)
- **2026-05-22** — Phase 1 explicit: strip ALL HTML to plain text. `tagged_text` is plain text + `<cited>` tags only. Paragraph structure preserved via newlines; no other HTML formatting carries through.
- **2026-05-22** — Phase 1 captures ALL citation forms (full + short + Id + supra) via uniform `<span class="citation">` matching. Each occurrence becomes its own `<cited>` tag.
- **2026-05-22** — Per-occurrence ids + per-cluster grouping hint on tags: `<cited id="N" group="gM">`. `id` is unique per occurrence (sequential 0,1,2,…); `group` is the per-cluster local handle derived from CL's `data-id` (sequential g0,g1,g2,…). Spans sharing a `data-id` get the same `group`; unmatched/ambiguous spans each get a solo group. The LLM treats `group` as a hint and can split, merge, or add as needed. Replaces the earlier "per-case ids" proposal — per-occurrence + group gives the LLM distinguishable handles for regrouping.
- **2026-05-22** — Phase 1 output adds a top-level `groups` map (`{group_id → [ids]}`) for downstream convenience. Each per-id entry in `id_to_metadata_map` also carries `group_id`.
- **2026-05-22** — `data-id` (CL's cluster_id) is used internally in Phase 1 as the grouping key but dropped from the persisted artifact. The renamed `group_id` (g0, g1, …) is purely a local handle within a cluster and cannot be confused with cluster_id.
- **2026-05-22** — Phase 1 uses tree-walk (not string matching) to record citation span offsets from `html_with_citations`. String matching is unreliable for short cites like "Id." and "supra" — too few chars, can match non-citation occurrences in prose. Walking the BeautifulSoup parse tree gives exact offsets in one pass, regardless of citation form. String matching survives only in the UnmatchedCitation safety-net path, where the citation strings are long and distinctive enough for cursor-based matching to be reliable.
- **2026-05-22** — `groups` summary map dropped from Phase 2 LLM input. The cluster-wide summary would have created a contradictory instruction in multi-chunk calls ("verify these ids — but don't touch the ones you can't see"). Cross-chunk merging is now postprocess's job: a two-pass merge (1) by shared `group_id` from `id_to_metadata_map`, then (2) by canonical `(mainCitationString, caseName)`. The inline `<cited id="N" group="gM">` attribute stays — the model still sees CL's grouping signal for tags visible in its chunk. The summary map remains in the Phase 1 artifact on disk for downstream consumers / debugging, just not in the LLM input.
- **2026-05-26** — Duplicate `010combined` records kept in Phase 1, deduplicated in Phase 2. A Phase 1 spot-check found that 28% of the 0410 benchmark (109/383 clusters) carries both split opinion records and a near-duplicate `010combined` re-render (CL ingested the same opinions from two sources). Investigation: after normalizing OCR spacing, 103/109 are >70% pure duplicates; the other 6 differ only by parallel-reporter density and caption boilerplate, not new cases or treatment-relevant prose. Across all 109, exactly one combined-unique named case exists (`Terrell v Garcia, supra` — already cited in full elsewhere). A prose-shingle diff confirmed the analytical text around each citation is identical between renderings. **Decision:** keep Phase 1 faithful to source (no drop — matches the line-number-noise philosophy; the migration is not cost-constrained), let Phase 2's case-level two-pass merge collapse the duplicates, and make `section_role` + Phase 5 window-selection prefer split records over `010combined`. Phase 7 measures keep-vs-drop impact on treatment labels; drop combined there only if it provably changes zero labels.
- **2026-05-27** — `section_role` is not a Phase 2 field. Phase 2 doesn't know the treatment, so it can't pick "the winning occurrence" — that's a most-negative collapse decision. Phase 2 emits the full `occurrences[]` list (each with its `opinion_type` preserved); the single `section_role` is set at the Phase 5/6 collapse as the `opinion_type` of the occurrence whose treatment won (mapped to `lead`/`concurrence`/`dissent`/etc., split records preferred over `010combined`). This aligns `section_role`, `supporting_quote`, and `section_context` to all describe the same winning occurrence, per the 2026-05-05 output-spec decision that supporting_quote/section_context relate to the most-negative treatment.
- **2026-05-28** — Phase 2 prompt + schema refinements: (a) `mainCitationString` MUST include the year in parentheses, with only the primary reporter; (b) new `parallelCitationString` field (nullable, comma-separated reporter cites only — no name, no year); (c) `caseName` kept as a separate field for a postprocess consistency double-check (rather than dropped + extracted from `mainCit` via regex) because the parser's tag boundaries are inconsistent — sometimes wrapping only the reporter, sometimes the whole citation, sometimes cutting a case name in half on a short cite (e.g., `Cantwell v. <cited>Connecticut, supra, at 303</cited>`); (d) a new "Same-case identification" prompt section makes the confirm/split/merge criteria explicit (reporter > caseName tie-break, parallel-cite recognition, parenthetical/string-cite handling); (e) the "What the tag wraps" section is intentionally general — instead of enumerating tag shapes, instructs the model to read prose to the LEFT and RIGHT of every tag for case name, year, and parallel-cite context; (f) new audit log `grouped_casename_warnings.jsonl` records consistency failures (caseName not in mainCit; merged caseNames disagree).
- **2026-05-28** — Opinion-aware packing in `prepare_calls` (replaces naive across-the-board FFD). Principle: prefer whole opinions, accept more calls. Rules: (a) non-combined opinions above `MAX_OPINION_TOKENS` (150K) → each chunk gets its own dedicated single-item call; (b) whole non-combined opinions are FFD-packed together within `PER_CALL_BUDGET_TOKENS` (170K); (c) combined opinions are handled independently — whole = own call, chunked = own dedicated calls per chunk; (d) combined is never mixed with non-combined in the same call, because the ~28% combined-as-rerender pattern would otherwise cause intra-call duplication.
- **2026-05-28** — Merged `CHUNK_THRESHOLD_TOKENS` + `CHUNK_SIZE_TOKENS` into a single `MAX_OPINION_TOKENS = 150_000` constant (and `prepare_calls`'s two params `threshold_tokens` + `chunk_size_tokens` into one `max_opinion_tokens`). Conceptually they're the same number under the opinion-aware rule: "max content per opinion in a call." 150K sits 20K under the 170K `PER_CALL_BUDGET_TOKENS` for safety headroom. With this threshold, no opinion in the 0410 benchmark triggers chunking (largest is 96K), so every cluster ends up with at most 2 calls (1 packed non-combined + 1 whole combined).
- **2026-05-28** — `citation_grouping` prompt rewritten from scratch + new `rejected_ids` schema field. **Failure modes observed** in the 3-cluster smoke run that drove the rewrite: (a) **over-rejection** — 90/755 ids rejected on a single SCOTUS opinion, 100% of which were legitimate citations (bare reporter cites, pin cites, bare case-name shortenings, Id./Ibid., supra); (b) **zero untagged additions** even on noisy trial-court text where eyecite is known to miss things; (c) prior prompt's "Add untagged" was permissive ("you may"), not directive. **Design pivot**: (1) reframe model role as ROUTER (every tagged id must end up somewhere) instead of EDITOR (curate a clean catalog); (2) **schema change** — add top-level `rejected_ids: [{id, reason}]` and require it (silent omission is now a postprocess-detected bug); (3) make scanning for untagged citations a sequenced STEP after grouping, with concrete patterns to look for; (4) decision-tree language (Q1/Q2) over bullet rules; (5) replace all real cases with fictitious ones (Talbot v. Henning, Wendell v. Carson, Apex Corp. v. Northwood, Doe v. Roe, Acme v. State). Postprocess now distinguishes `n_explicit_rejections` (model in `rejected_ids` with reason) from `n_silent_omissions` (model omitted entirely — bug under new prompt); legacy `n_implicit_rejections` is kept as the sum for backward compat. Prompt grew from ~3.8K → ~4.1K tokens, still under the 5K budget.
- **2026-05-28** — Prompt task section restructured to a 4-step ordered workflow: **SCAN → GROUP → REJECT → VERIFY** (was: group first, scan later). New explicit category in scan-step: **untagged narrative references** ("in the Powell case", "the Powell Court held", "Powell's reasoning") which the parser cannot pattern-match but the model can identify by reading context. These attach as `untagged_occurrences` on the existing case row when the narrative refers to a previously-cited case. Adding the narrative refs BEFORE grouping ensures they're grouped consistently with the same-case identification rules; if added after grouping they'd be afterthoughts. Same-case rules updated to include narrative-ref matching as another way two references can be the same case. Worked example added showing narrative attachment. Prompt now ~4.9K tokens.
- **2026-05-28 (second pass)** — Three coordinated changes after the first re-run on the SCOTUS 117935 cluster still showed 109/755 silent omissions and 0 untagged additions:
  - **Smaller chunks, more overlap.** `MAX_OPINION_TOKENS`: 150K → **20K**. `CHUNK_OVERLAP_TOKENS`: 2K → **5K**. Smoke run showed Haiku 4.5 attention diluting on a 92K-token chunk with 755 ids. 20K trades calls for focus.
  - **One opinion per call.** Dropped FFD bin-packing entirely. With small chunks, packing 8 short opinions into one 170K call recreates the dilution problem the smaller chunks are meant to fix. `binpack_first_fit_decreasing` removed; `prepare_calls` now emits one call per opinion (or per chunk of a long opinion). Combined-vs-non-combined isolation is automatic under this rule.
  - **No rejection.** Trusting eyecite's precision (Phase 1 spot-check found no false positives in the inserted tags), `rejected_ids` is dropped from the schema and the prompt. Every tagged id MUST route to some `cited_case`; ambiguous ids land in a `cited_case` with null `mainCitationString` / `caseName` rather than being omitted. The cost asymmetry justifies removing the affordance: a bogus trial-court line-number tag slipping through = one extra row with null names (caught in Phase 7 spot-check); a real citation rejected = lost data. The 90-then-109 over-rejection bug is fixed by removing the option.
  - **Tightened scanning prompt.** Step 1 (SCAN) was rewritten to be directive ("MUST scan top-to-bottom") with concrete patterns and a backreference category ("there, the Court held"). Worked example shows attaching multiple narrative refs to one case row. Aimed at the zero-untagged-additions bug across all 3 smoke clusters.
  - **Id-conflict resolution.** New postprocess pass (Step 2c) detects ids assigned to multiple final cited_cases (overlap-region disagreement) and picks a winner by occurrence count → mainCit length → index. Loser cases have the id stripped; losers reduced to zero accepted_ids + zero untagged are dropped. New audit log `grouped_id_conflicts.jsonl`. Required because chunking + overlap multiplies the surface for this kind of disagreement, and Pass 1/2 don't catch it when the merged cases have legitimately different canonical names.
  - **Unrouted-id fallback.** New postprocess pass (Step 2d) attaches model-omitted ids to a sibling case sharing the same Phase 1 `group_id`, falling back to a solo case with null names when no sibling exists. Phase 1's grouping is treated as authoritative for which cited_case an omitted id belongs to; the LLM's role narrows to providing names + untagged finds. Even when the model misbehaves, every Phase 1 id ends up routed in the final output. Log entry now records `fallback: "group_id_sibling"` or `"solo_from_phase1"`.
  - **Postprocess result schema.** `n_explicit_rejections` / `n_silent_omissions` / `n_implicit_rejections` removed. Replaced with `n_unrouted_ids` (count of model-omitted ids restored via fallback) + `n_id_conflicts`. Audit log `grouped_rejected_ids.jsonl` → `grouped_unrouted_ids.jsonl`.

## Out of scope

- Replacement of eyecite with a fine-tuned encoder (issue #62; defer until eyecite recall is shown to be the binding constraint).
- UI changes in citator-demo-site beyond the JSON schema swap.
- Cluster cross-check.
- Detection of silent OCR errors both extractors miss.
- **Cited-case cluster resolution / linking cited cases to CL cluster_ids.** Out of scope for this project.
- Opinion summary enrichment (see Future ideas).
- **Line-number stripping for trial/district-court text.** Trial-court documents (`100trialcourt`) carry left-margin line numbers baked into the source text, which (a) corrupt CL's UnmatchedCitation rows — harmless, our safety net rejects them — and (b) inject noise into `tagged_text` (split citation strings, mangled docket numbers) that flows into Phase 5 offset windows + Phase 4 eyecite audit. Decision 2026-05-26: leave Phase 1 faithful to source; rely on Phase 2/5 LLM prompts to cope; revisit only if Phase 7 validation shows measurable harm. Not stripped, no issue filed.
