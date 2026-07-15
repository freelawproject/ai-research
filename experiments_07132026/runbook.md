# Runbook — SCOTUS↔Circuit linkability

`readme.md` is the canonical results narrative. This runbook is how to reproduce
them. All query scripts run in the `cl-django` container against CLReplica; the
linkage pipeline's stage 2 runs here in ai-research. Outputs land in `data/`
(gitignored).

## Query scripts (run in cl-django)

Stage each script into the container, then exec it:

```bash
docker exec cl-django mkdir -p /tmp/appellate_chain
docker cp <script>.py cl-django:/tmp/appellate_chain/<script>.py
docker exec cl-django python manage.py shell -c \
  "exec(open('/tmp/appellate_chain/<script>.py').read())"
docker cp cl-django:/tmp/appellate_chain/. ./data/     # pull outputs
```

| Script | Answers |
|---|---|
| `q1_record_counts.py` | Q1 — SCOTUS + per-circuit docket/cluster counts |
| `q2_valid_docket_numbers.py` | Q2 — docket-number validity via CL's cleaner |
| `q3_scotus_lower_court_info.py` | Q3 — SCOTUS w/ OCI + circuit `appeal_from` (linkable set) |
| `q3b_oci_decomposition.py` | Q3b — why only 21% has OCI (scrape coverage × era) |
| `q3c_modern_uncovered_diagnosis.py` | Q3c — modern-uncovered = dup vs boundary vs gap |
| `q4_duplicates.py` | Q4 — duplicate clusters (same court+date+docket#) |
| `q6_circuit_linkable.py` | Q6 — circuit-side match-target pool by era |
| `q7_downward_match.py` | Q7 — THE MATCH: SCOTUS-linkable ↔ circuit (+ era) |
| `q8_match_cardinality.py` | Q8 — cardinality (1:1 / 1:many / … / recoverable) |

Q7/Q8 each do a full pass over ~1.37M circuit clusters to build the index (a few
minutes). Q2/Q4 iterate all clusters (chunked with `.iterator()`).

## Producing the actual linked records → see `linkage/README.md`

The reusable 2-stage pipeline emits `links.json` (52,357 SCOTUS→circuit records)
+ a clickable `inspect_links.html`. Stage 1 (`link_extract.py`, cl-django) snapshots
the data; stage 2 (`link_build.py`, standalone) matches and writes the links. Re-run
stage 1 after a re-query, then stage 2.

## Not run / superseded

- `q5a_map_opinion_ids.py` + `q5b_embedding_similarity.py` — an embedding-cosine
  path to disambiguate Q4 duplicates. Scoped but **superseded** by the direct
  docket match (Q7/Q8) and the linkage pipeline; Q5b was never run. Kept for the
  future dedup / disambiguation lever.
- **Q2b** (LLM-clean cost estimate) — described in `plan.md`, not built.

## Notes
- Primary unit is `OpinionCluster`; docket counts are context only.
- Scripts are idempotent (overwrite their outputs). `data/` is gitignored.
