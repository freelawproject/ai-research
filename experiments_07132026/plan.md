# Appellate Chain Linkability — SCOTUS ↔ Circuit Feasibility (CLReplica)

## Goal

Measure how many SCOTUS and federal-circuit opinion records in the **current**
CLReplica can be linked SCOTUS → circuit, and how much of that is blocked by
missing/invalid docket numbers, missing lower-court info, and unresolvable
duplicates. Data has changed since `prior_experiments/experiments_718`, so every
number here comes from a fresh CLReplica query — no reuse of the 2025 counts.

Scope: **SCOTUS → circuit only.** Circuit = the 13 federal appellate courts
(`ca1`–`ca11`, `cadc`, `cafc`). District courts are out of scope for this pass.

## Current CL data model (post-merge, Alberto's SCOTUS ingest + docket cleaning)

The link no longer needs the ai-research SCOTUS scrape as a bridge — the
lower-court reference is now ingested into CL itself:

- `Docket.court` — the court (e.g. `scotus`, `ca9`).
- `Docket.docket_number` — cleaned docket number; `Docket.docket_number_raw` — raw.
- `Docket.appeal_from` (FK → Court) — the lower court the case was appealed from.
  `Docket.appeal_from_str` — string fallback.
- `Docket.originating_court_information` (1:1 → `OriginatingCourtInformation`).
  - `OriginatingCourtInformation.docket_number` — **the lower-court docket number**
    (cleaned); `.docket_number_raw` — raw. This is the SCOTUS→circuit join key.
- `OpinionCluster` (per docket) — `date_filed`, `case_name`, `precedential_status`.
- `Opinion` (per cluster) — text (`plain_text` / `html_with_citations`), for Q5.

The linking mechanism (once feasibility is confirmed): for each SCOTUS docket
with lower-court info, match `OriginatingCourtInformation.docket_number` against a
circuit `Docket.docket_number` where `circuit.court == scotus.appeal_from`, using
the **same cleaned/normalized docket numbers** the merged docket-cleaning code
produces. This experiment quantifies the ceiling and the friction of that match.

## Unit of counting

Primary unit = **OpinionCluster** (one opinion record = one linkable node).
Dockets reported alongside as context (a docket may carry 0..n clusters).

## Definitions (defaults — flag if these should change)

- **Valid docket number** = `docket_number` non-empty AND parses to a recognized
  format. SCOTUS: N-type (`NN-NNNN`) or A-type (`NNANNN`) after cleaning. Circuit:
  non-empty after cleaning; a single-value or explodable list (drop pure-garbage /
  unparseable). Reuse the classifier logic from the merged CL cleaner /
  `prior_experiments/experiments_915/clean_utils.py`.
- **Valid lower-court info** (SCOTUS side) = `originating_court_information`
  exists AND `OriginatingCourtInformation.docket_number` non-empty AND
  `appeal_from` resolves to a court (ideally one of the 13 circuits).
- **Duplicate** = 2+ clusters sharing the identical triple
  `(court_id, date_filed, cleaned_docket_number)`.
- **Significant content difference** (Q5) = concatenated opinion text of the
  clustered opinions differs beyond a similarity threshold (method TBD — see
  Open Decisions).

## The five questions → query design

Each step is a self-contained script run in the `cl-django` container against
CLReplica (see Execution). Outputs land in `data/` (gitignored) as JSON/CSV.

### Q1 — How many SCOTUS and circuit records are in CL
- `Docket.objects.filter(court__id="scotus")` → count dockets; count clusters via
  `OpinionCluster.objects.filter(docket__court__id="scotus")`.
- Same for `court__id__in=CIRCUITS` (13 courts). Report per-court breakdown.
- Output: `q1_record_counts.json` (dockets + clusters, SCOTUS + each circuit).

### Q2 — How many contain valid docket numbers
Reuse **CL's own merged cleaner** (`cl.search.docket_number_cleaner.
clean_docket_number_raw`) rather than an ad-hoc regex, so "valid" means what
production means. Per cluster it returns one of: `regex_cleaned` (handled),
`needs_llm` (regex couldn't standardize — the LLM-fallback set), or `None`
(empty raw / unsupported court). `court_map` supports scotus + cadc + ca1..ca11;
**cafc is not in the map** → counted as `unsupported_court` (gap to flag).
- Source string = `docket_number_raw` if populated else `docket_number` (flag is
  OFF, so `docket_number` still holds the original).
- Output: `q2_valid_docket_numbers.json` (buckets + samples per bucket).

### Q2b — Cost to LLM-clean docket numbers (4o-mini)
After Q1 (+ Q2's `needs_llm` count), estimate the OpenAI cost to clean docket
numbers via `gpt-4o-mini`, matching the production LLM step. Two scenarios:
- **Realistic** — clean only the `needs_llm` set (what the pipeline actually
  sends to the LLM).
- **Upper bound** — run *everything* (all SCOTUS+circuit records from Q1)
  through 4o-mini.
Cost model mirrors the daemon: **batch size 10** dockets per call sharing the
`F_PROMPT` system prompt, structured JSON output.
`per_batch_input ≈ tokens(F_PROMPT) + 10·tokens(one docket string)`;
`per_batch_output ≈ 10·tokens(one cleaned result)`;
`batches = ceil(records / 10)`; multiply by 4o-mini input/output rates.
Measure `F_PROMPT` and sample docket-string token counts with `tiktoken`
(don't hardcode). Note: production uses a *cascade* (4o-mini + 4.1-mini
consensus → 4o + 4.1 → tie-breaker), so single-4o-mini is a floor for the
realistic scenario — report the cascade multiplier as a caveat.
- Output: `q2b_llm_cost_estimate.json` + a short table in `readme.md`.

### Q3 — SCOTUS records w/ valid docket numbers that have valid lower-court info
- Among SCOTUS clusters with a valid docket number, count those whose docket has
  `originating_court_information.docket_number` populated AND `appeal_from` set.
- Break down by: appeal_from is one of the 13 circuits vs other court vs only
  `appeal_from_str` vs none. **This also tells us whether Alberto's ingest
  (#6954) has actually populated OCI in CLReplica yet** — the gating unknown.
- Output: `q3_scotus_lower_court_info.json`.

### Q4 — Duplicate SCOTUS / circuit cases (same court, date, docket number)
- Group clusters by `(court_id, date_filed, cleaned_docket_number)`; count groups
  with size > 1 and total clusters in them. Report separately for SCOTUS and
  circuit. These are the cases where a 1:1 match cannot be made confidently.
- Output: `q4_duplicates.json` + `q4_duplicate_groups.csv` (the colliding
  cluster ids/case names per group, for Q5).

### Q5 — Of the duplicates, how many are likely *different* cases (content differs)
Use the **existing opinion embeddings in S3** (generated from `html_with_citations`,
chunked) rather than re-fetching/aligning raw text.

- **Source:** `s3://com-courtlistener-storage/embeddings/opinions/`
  `freelawproject/modernbert-embed-base_finetune_512/{opinion_id}.json`.
  Each file: `{"id": opinion_id, "embeddings": [{"chunk_number", "chunk",
  "embedding": [...]}, ...]}`. **Keyed by `opinion_id`, not cluster** — so map
  each duplicate cluster → its `Opinion` rows → embedding files.
- **Per cluster:** gather the cluster's opinion chunks in order
  (opinion order, then `chunk_number`), take the **first 10 chunks**, and
  **mean-pool** their `embedding` vectors → one vector per cluster.
- **Per group:** cosine similarity between the pooled cluster vectors; label the
  group "distinct cases" if min pairwise cosine < threshold, else "true
  duplicate." Report the full similarity distribution so the threshold is
  data-driven (legal opinions are highly similar in general, so expect a high
  cutoff; calibrate on a small hand-checked sample).
- **Access:** boto3 from ai-research (`profile_name="dev-env"`, same as Bedrock)
  reading bucket `com-courtlistener-storage`. Missing embedding files are
  counted as an "unresolved" bucket, not silently dropped.
- Output: `q5_duplicate_content.json` (per-group min cosine + label + missing
  count) + `q5_examples.csv` (worked examples both ways for manual spot-check).

## Execution

Scripts import `cl.search.models` and run via:

```
docker exec cl-django python manage.py shell -c "exec(open('<script>.py').read())"
```

against CLReplica (same path as `experiments_05262026/fetch_cl_data.py`). Heavy
steps (Q2 cleaning, Q5 text pull) chunk over dockets and save incrementally.
All imports at top of file (project rule).

## Deliverables

- `plan.md` (this file).
- Query scripts `q1_*.py` … `q5_*.py` (self-contained, run in `cl-django`).
- `data/` outputs per question (gitignored).
- `readme.md` — final numbers + a short linkability verdict (ceiling count,
  friction breakdown, recommended next step for the CL-side linking command).

## Open decisions (to settle before building)

1. **Q5 method — SETTLED:** embedding cosine similarity (mean-pool of first 10
   chunks per cluster) from the S3 opinion embeddings. Threshold to be calibrated
   on a hand-checked sample from the observed distribution.
2. **Q5 pooling scope** — first 10 chunks across *all* the cluster's opinions
   (lead + concurrence/dissent, in order) vs the lead/primary opinion only.
   Default: across all opinions, in order.
3. **Valid docket number — SETTLED:** use CL's merged `clean_docket_number_raw`
   (regex_cleaned / needs_llm / unsupported), not an ad-hoc regex.
4. **Q2b cost scenario** — realistic (needs_llm only) vs upper-bound (all
   records); report both. Cascade vs single-4o-mini multiplier TBD.
5. **Execution owner — SETTLED:** Rachel runs the `cl-django`/CLReplica queries
   and holds S3 read access; I write the scripts + runbook.
