# SCOTUS↔Circuit Linkability — Results (CLReplica, 2026-07-14)

Feasibility of linking SCOTUS opinions down to the circuit opinion below, using
the current in-database mechanism (`OriginatingCourtInformation.docket_number` +
`Docket.appeal_from`). Unit = OpinionCluster. Circuit = 13 fed appellate courts.

## TL;DR
Of ~500K SCOTUS opinion clusters and ~1.37M circuit clusters, **~79K SCOTUS cases
carry the lower-court info needed to reach a circuit, and ~50K already match a
circuit case by docket number today** (~52K best case, after docket-number cleaning
+ de-duplication). This covers **SCOTUS→circuit only** — the easy half, since both
share the same docket-number format; extending the chain **circuit→district** needs
the FJC Integrated Database and de-normalizing FJC's coded docket numbers (see the
FJC section at the end).

> **Caveat:** hastily assembled with Claude on top of the 2025 analysis — a
> directional feasibility read, **not a validated deliverable**. The matches carry
> known false positives (docket-number reuse across years; no date/party guard yet),
> and the ~27K "unmatched" mixes true coverage gaps with data-quality misses.
> Turning this into a real deliverable needs a few days to validate matches and
> quantify the error rate.

## Q1 — Scale

| Population | Dockets | Clusters |
|---|---|---|
| SCOTUS | 589,706 | 499,543 |
| Circuit (13 courts) | 1,566,255 | 1,372,510 |

## Q2 — Docket-number validity (via CL's merged `clean_docket_number_raw`)

| Bucket | SCOTUS | Circuit |
|---|---|---|
| regex_cleaned | 467,912 (93.7%) | 1,199,995 (87.4%) |
| needs_llm | 22,088 (4.4%) | 115,982 (8.5%) |
| empty | 9,543 (1.9%) | 1,794 (0.1%) |
| unsupported_court | 0 | 54,739 (4.0%) — **all cafc** |
| **valid (regex+llm)** | **490,000 (98.1%)** | **1,315,977 (95.9%)** |

- Docket cleanliness is **not** the bottleneck (~96–98% valid).
- **cafc gap:** cafc is not in the cleaner's `court_map`, so all 54,739 cafc
  clusters are skipped. Easy CL-side fix (add `cafc` → FEDERAL_APPELLATE).
- `needs_llm` set for the cost analysis (Q2b): 22,088 + 115,982 = **138,070**.

## Q3 — SCOTUS lower-court info (denominator = 490,000 valid-docket SCOTUS)

| Field | Count | % of valid |
|---|---|---|
| has originating_court_information | 104,423 | 21.3% |
| OCI docket_number present | 104,390 | 21.3% |
| appeal_from resolved to a Court | 94,600 | 19.3% |
| — of which circuit | 78,865 | 16.1% |
| — of which non-circuit (state high courts, etc.) | 15,735 | 3.2% |
| appeal_from_str only (unresolved court) | 9,834 | 2.0% |
| **linkable (OCI docket# AND appeal_from∈circuit)** | **78,861** | **16.1%** |

- **OCI is populated, and the ~21% is largely structural — not fixable by
  re-running #6954.** Per #6954, the importer's universe is a fixed inventory of
  **191,442** scraped modern SCOTUS cases; its first pass is DONE (191,406
  merged, 99.98%). So:
  - **~300k** of CL's 499,543 SCOTUS clusters predate the scrape inventory →
    can never get OCI from this importer (era/coverage limit). See Q3b.
  - Of the ~191k merged, only ~104k (`has_oci`) had lower-court info in the
    scrape (original-jurisdiction / applications / no lower court listed).
  - SCOTUS-side scrape↔CL matching is NOT the loss (191,406/191,442 matched);
    docket-number differences bite on the *downward* match (next experiment).
- **Realistic growth is bounded:** the ~9,834 `appeal_from_str_only` ≈ the ~10K
  courts-db resolution failures in #7099 (446 unknown courts, courts-db #115).
  After those land + re-ingest, the circuit-appeal ones become newly linkable.
  The pre-scrape ~300k need the old scrape-bridge or another lower-court source.
- Current in-DB ceiling for SCOTUS→circuit = **~78,861** SCOTUS clusters. Cross-
  validates the 2025 scrape-based approach (~74,125), which is reassuring.
- 15,735 SCOTUS cases appeal from state/other courts (out of scope here).

## Q3b — Why only 21% has OCI: it's scrape coverage, full stop

Crosstab of valid SCOTUS clusters (in scrape universe = has `ScotusDocketMetadata`):

| | has OCI | no OCI | total |
|---|---|---|---|
| **in scrape** | 104,423 | 2,816 | 107,239 (21.9%) |
| **not in scrape** | **0** | 382,761 | 382,761 (78.1%) |

- Within the scrape universe: **97.4% have OCI**. Outside it: **exactly 0**.
- The missing 78% is purely *not scraped* — not docket mismatch, not quality.
- OCI rate by decade: ≤1990 ≈0%, 2000s 38.7%, 2010s 72.4%, 2020s 78.7%. Hard
  wall at ~2000 (SCOTUS docket-search coverage boundary); modern era only
  partially covered.
- Note: importer merged 191,406 *dockets* but only 107,239 map to a valid-docket
  *cluster* (rest are docket-only: applications / cert denials w/o an opinion).

**The not-in-scrape 382,761 split by era** (from the decade cut):

| Era | valid clusters | in scrape (~) | not in scrape (~) |
|---|---|---|---|
| ≤1999 | 301,684 | ~50 | **~301,600** |
| 2000+ | 188,316 | ~107,190 | **~81,120** |
| **total** | 490,000 | 107,239 | 382,761 |

Modern-era scrape coverage is itself partial: ~40% of 2000s, ~74% of 2010s,
~81% of 2020s.

- **~301,600 (pre-2000): structurally NOT linkable** — the SCOTUS docket-search
  scrape doesn't go back that far. Needs a different lower-court source.
- **~81,120 (2000+):** ~75% are duplicate CL copies of scrape-covered cases
  (Q3c) — **recoverable via dedup** (the lower-court info exists on the sibling
  copy); whether each is net-new vs. redundant with an already-linked copy is
  unmeasured. The rest are boundary-era.

## Q3c — Why the 81,122 modern (2000+) clusters aren't covered

Tested each uncovered modern cluster's `docket_number_core` against the set of
cores that DID get merged (scrape matches on core):

| Core status | count | % |
|---|---|---|
| core_dup_of_covered (core matches an in-scrape docket) | 60,792 | 74.9% |
| core_not_covered (non-empty core, no match) | 16,234 | 20.0% |
| no_core | 4,096 | 5.0% |

- **75% are DUPLICATE CL dockets of covered cases** (e.g. `02-8636` whose core
  matches a scrape-covered docket) — the case *is* covered; CL just holds a
  second copy that missed the metadata. Confirms Q4's SCOTUS duplication.
  **Recoverable via dedup** (info is on the sibling copy); net-new vs. redundant
  with an already-linked copy is unmeasured (needs a sibling-has-cluster check).
- **20% not-covered** samples are `99-2035`, `98-1189`, `99-6615`… — docketed
  1998–1999, decided 2000 → they fall just below the scrape's start (~Term
  2001). Same coverage-boundary, not a merge failure. (Genuinely-missing clean
  modern `NN-NNNN` cases are negligible.)
- **Conclusion:** re-running #6954 recovers none of this from the *scrape*
  (dupes' cases already covered; boundary cases outside the inventory). But the
  ~61K duplicates are **linkable via CL-side dedup** (the info already exists on
  the sibling), so the linkable set could grow beyond 78,861 by however many
  duplicates are net-new (unmeasured). Other lever: #7099/courts-db #115
  (~9,834 unresolved courts).
- Caveat: the per-format sub-tally (NN-NNNN vs "other") looked inconsistent with
  the samples and was not relied upon; the core-status split is set-based/robust.

## Q4 — Duplicate ambiguity (same court + date + docket# → no confident 1:1)

| | SCOTUS | Circuit |
|---|---|---|
| groupable clusters | 490,000 | 1,370,713 |
| duplicate groups (size>1) | 76,013 | 65,410 |
| clusters in duplicate groups | **153,010 (31.2%)** | **133,548 (9.7%)** |
| max group size | 5 | 10 |

- SCOTUS duplication is high (31%). Q4 can't tell true-duplicate (same case
  ingested twice → safe to collapse) from genuine distinct-case collisions
  (harmful) — that's Q5's job.

## Q5a — Ready for embedding disambiguation

All 286,558 duplicate-group clusters mapped to opinions (avg 1.04 each) →
`q5_opinion_ids.json`. Q5b (embedding cosine) not yet run.

## Q7 — The intersection (THE CONCLUSION): SCOTUS-linkable × circuit DB

Of the 78,861 SCOTUS-linkable clusters, how many actually match a circuit
`(court, docket #)` pair in CL (both sides cleaned the same way; no circuit-side
date filter):

| Outcome | count | % of linkable |
|---|---|---|
| **matched to a circuit (court, docket#)** | **50,043** | **63.5%** |
| — unambiguous (one circuit cluster) | 37,898 | 48.1% |
| — ambiguous (>1 circuit cluster) | 12,145 | 15.4% |
| (+ gained by suffix leniency) | +1,615 | +2.0% |
| unmatched | 27,203 | 34.5% |

- **End-to-end: ~50,043 SCOTUS clusters match a circuit case in CL today** —
  63.5% of linkable, **~10% of all 499,543 SCOTUS clusters**. Confident (1:1)
  count ≈ **37,898** after setting aside the 12,145 ambiguous.
- **Suffix mismatch is no longer the villain:** leniency adds only ~1,615 (2%),
  vs the ~65K it cost the 2025 run — the CL cleaner + clean OCI numbers handle it.
- **~24% of matches are ambiguous** (pair hits >1 circuit cluster, Q4
  duplication) → need Q5b disambiguation before a confident 1:1.
- **~34.5% unmatched:** SCOTUS names a circuit docket the match can't resolve to
  a circuit cluster. This is a MIX of true coverage gaps (opinion not in CL) and
  data-quality misses (opinion exists but its `docket_number` field differs from
  the cited docket, so a field match misses it — confirmed by spot-checks). We
  can't split the two without a more systemic analysis (FJC cross-check + a
  fuzzier party/date/full-text match).
- **Era confirms the lag:** matched circuit cases are 2000s (19,709) + 2010s
  (29,326) with a ~400 pre-2000 tail; SCOTUS decisions skew a decade later. A
  hard post-2000 circuit floor would have dropped the pre-2000 matches. OCI
  lower-court judgment dates agree (7,178 matched had no recorded lower date).

## Q8 — Match cardinality (best-case linkage)

Of the 78,861 SCOTUS-linkable clusters, the match broken down by cardinality
(C = # circuit clusters on the matched docket pair, S = # SCOTUS clusters on it):

| Match type | count | % of linkable |
|---|---|---|
| one-to-one (C=1, S=1) | 37,499 | 47.6% |
| many-to-one (C=1, S>1) | 1,201 | 1.5% |
| one-to-many (C>1, S=1) | 10,086 | 12.8% |
| many-to-many (C>1, S>1) | 1,257 | 1.6% |
| recoverable by cleaning (digit-core only) | 2,314 | 2.9% |
| unmatched | 26,504 | 33.6% |

- **Best case ≈ 52,357 (66% of linkable):** everything except unmatched
  (matched_exact 50,043 + cleaning-recoverable 2,314).
- **~38,700 are definite one-to-one** (37,499 + 1,201 many-to-one, where the
  circuit target is still unique) — the solid core.
- **~11,343 need dedup** (one-to-many 10,086 + many-to-many 1,257).
- **Docket cleaning is a small lever:** only 2,314 recover by cleaning, and just
  5,436 SCOTUS OCI dockets are `needs_llm` (372 of the recoverable set, 1,832 of
  the unmatched). The dominant loss is the **33.6% unmatched** — but that's a
  MIX of true coverage gaps and data-quality misses (docket field ≠ cited docket,
  per spot-checks), not established to be one or the other; splitting them needs
  a systemic analysis.

## Q6 — Circuit-side match-target pool (by era)

Over all 13 circuits (~1,372,581 clusters):

| Bucket | count | % |
|---|---|---|
| valid docket, 2000+ | 631,398 | 46.0% |
| valid docket, pre-2000 | 684,645 | 49.9% |
| cafc (cleaner-unsupported) | 54,744 | 4.0% |
| empty docket | 1,794 | 0.1% |

- Docket cleanliness is a non-issue on the circuit side too — **95.9% valid**,
  and docket-validity is ~94–100% in every decade from 1890 on. Unlike SCOTUS,
  there is no era cliff in *validity*.
- Era split ≈ **46% post-2000 / 50% pre-2000**. Post-2000 is the window the
  SCOTUS→circuit match draws from (Q7 matched ~50K SCOTUS cases into it); the
  pre-2000 half is the target for the **circuit→district** layer via FJC (1971+),
  not dead weight.
- **cafc (54,744) is entirely uncleaned** — not in the cleaner's `court_map` (the
  Q2 gap); 39,681 of it is post-2000.
- The ~50K matched circuit cases (Q7, almost all 2000s–2010s) are a small slice
  of the 631K post-2000 pool — only a fraction of circuit cases are ever reviewed
  by SCOTUS; the rest link downward, not upward.

## Verdict + next steps

- **Viable, data-limited.** The bottleneck is OCI coverage (21%), **not** docket
  cleanliness (96–98% valid). And most of that 21% cap is **structural** (scrape
  inventory = 191k modern cases of 499k clusters), so it won't move much by
  re-running #6954 — run Q3b to confirm the era-vs-quality split.
- **Data ceiling ~78,861** SCOTUS clusters *have* the info to attempt a link;
  **~50,043 actually match a circuit case in CL (Q7)** — the real end-to-end
  number; ~37,898 unambiguous.
- **Two levers on the ~50K:** (a) disambiguate the 12,145 ambiguous matches
  (Q5b embeddings) toward confident 1:1s; (b) recover from the 27,203 unmatched
  via FJC cross-check + docket-cleaning of the harder cases.
- **The match is now being measured (Q7):** of the ~78,861 SCOTUS-linkable
  clusters, how many have a real `(circuit court, docket #)` match in CL, with
  exact vs suffix-lenient vs ambiguous, plus the era of each matched circuit case.
- **Circuit-side pool (Q6):** valid-docket circuit clusters, by decade — the
  denominator. No hard post-2000 floor: a circuit decision can precede its SCOTUS
  review by years, so the relevant circuit-date window is set from Q7's observed
  lower-court decision dates, not a fixed cutoff.
- **Open:** Q2b LLM-clean cost (138,070 needs_llm + 54,739 cafc);
  Q5b run + threshold calibration.

## Second source: FJC Appeals IDB (district↔circuit — future layer)

Documented for later; not built in this experiment. The FJC Appeals Integrated
Database (prior work: `prior_experiments/experiments_826/`) is the natural source
for the **lower half** of the chain:

- **What it is:** ~1.99M cleaned appeals records, **1971–2025** (raw
  `ap71to07.txt` + `ap08on.txt`). Natively carries the originating **district**
  court (`DDIST` → CL district id, 100% mapped) and district docket (`DDOCKET`,
  ~97% populated) — the circuit→district link is in the data.
- **Complements, doesn't replace, the SCOTUS scrape.** FJC = circuit↔district
  (1971+); SCOTUS scrape/OCI = SCOTUS↔circuit (~2000+). **FJC has no SCOTUS
  fields**, so it can't do the SCOTUS↔circuit hop and does **not** raise the
  SCOTUS→circuit ceiling or reach the ~300K pre-2000 SCOTUS clusters.
- **Prior match (826):** FJC↔CL circuit clusters on `(circuit, cleaned appeals
  docket#)` → **75.8%** of CL circuit dockets matched FJC (46% the other way —
  CL only holds opinion-bearing clusters).
- **Uses:** (1) extend the chain down to district; (2) cross-check Q7's
  `unmatched` (FJC has *all* appeals → confirms an appeal existed vs a CL opinion
  gap); (3) disposition/outcome enrichment for the citator.
- **Remaining work / caveats:** `DDOCKET` never normalized and the join to CL
  *district* dockets never built (826 only did FJC↔circuit); FJC-vs-CL docket
  cleaners unreconciled. **Data location:** CL's in-DB `FjcIntegratedDatabase`
  (`cl/recap/models.py:512`) is the **Civil** district IDB, not Appeals — use the
  raw 826 files for the appeals data.
