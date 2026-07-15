# SCOTUS → Circuit linkage pipeline (reusable)

Two stages: **extract** (in `cl-django`, re-run when data changes) → **build links**
(standalone, no DB). Re-querying = re-run stage 1, then stage 2.

## Stage 1 — extract snapshots (cl-django / CLReplica)

```bash
docker exec cl-django mkdir -p /tmp/appellate_chain/linkage
docker cp link_extract.py cl-django:/tmp/appellate_chain/linkage/
docker exec cl-django python manage.py shell -c \
  "exec(open('/tmp/appellate_chain/linkage/link_extract.py').read())"
docker cp cl-django:/tmp/appellate_chain/linkage/. ../data/
```

Produces (in `../data/`):
- `scotus_linkable.json` — SCOTUS clusters with a circuit lower court + OCI docket #.
- `circuit_records.jsonl` — one line per circuit cluster (the match-target pool).

Each record carries `cluster_id`, docket number, `case_name`, `date_filed`, and
precomputed match `tokens` / `cores` (docket numbers cleaned with CL's own cleaner),
so stage 2 needs no CL dependency.

## Stage 2 — build links (standalone, stdlib only)

```bash
uv run python link_build.py --data-dir ../data      # or: python3 link_build.py --data-dir ../data
```

Produces (in `../data/`):
- `links.json` — every matched SCOTUS cluster → circuit cluster(s), with metadata
  and CourtListener URLs. Shape: `{match_type, scotus:{cluster_id,url,…}, appeal_from,
  oci_docket, n_lower_dockets_matched, n_circuit, circuit:[{cluster_id,url,…}]}`.
  `circuit` lists **every** matched opinion across **all** of the case's lower-court
  dockets (a consolidated appeal names several, e.g. `21-7127, 22-7019, 22-7020`);
  `n_lower_dockets_matched` is how many of those dockets found an opinion. The
  multi-docket string is auto-split into tokens — no separate cleaning needed.
  `match_type` still reflects the most specific single docket pair.
- `links_summary.json` — counts by match type.
- `links_sample.json` — a stratified sample: 5 per match type, plus 5 unmatched.
- `inspect_links.html` — self-contained page: open it and click through to CL to
  sanity-check each link.

## Match types

Per matched SCOTUS cluster, on its most specific pair (C = # circuit clusters on
that docket pair, S = # SCOTUS clusters on it):

| type | meaning |
|---|---|
| `one_to_one` | C=1, S=1 — definite, clean |
| `many_to_one` | C=1, S>1 — several SCOTUS point to one circuit case (still definite) |
| `one_to_many` | C>1, S=1 — one SCOTUS, several candidate circuit clusters (dedup needed) |
| `many_to_many` | C>1, S>1 — ambiguous both sides |
| `recoverable_by_cleaning` | no exact hit, but digit-cores match — recoverable via aggressive/LLM docket cleaning |
| `unmatched` | no circuit docket found (likely a CL opinion-coverage gap) |

## Notes

- CourtListener opinion URL: `https://www.courtlistener.com/opinion/<cluster_id>/<slug>/`
  (slug is derived from the case name; CL redirects to the canonical slug).
- `circuit_records.jsonl` is large (~1.37M lines). It lives in the gitignored
  `data/` dir; do not commit.
- Numbers reconcile with the Q7/Q8 analysis; this pipeline is the productionized,
  re-runnable version that emits the actual linked records.
