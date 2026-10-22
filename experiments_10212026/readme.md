# 1021 — External citator benchmark through the two-stage pipeline

Runs the 296 citing clusters of the Kirschenfeld + Hellyer human-verdict pairs (from
`flp/external-benchmark/output/external_benchmark.csv`) through the 0911 pipeline:
gate_v6 two passes (GPT-5.6 Luna, on demand) → labeler_v3 (Sonnet 4.6, Bedrock batch).
Dahl's 41 pairs are not run: their 17 citing clusters are benchmark clusters already covered
by the 0911 dev/test runs (see `flp/external-benchmark/output/dahl_four_way.csv`).

## Files

| file | role |
|---|---|
| `data/citing_cluster_ids.txt` | the 296 citing clusters (263 Hellyer, 33 Kirschenfeld) |
| `data/citing_metadata.csv` | case name / court / year per cluster (from the resolver's CL metadata); read by the 0911 build through `CITATOR_CITING_META_EXTRA` |
| `data/pairs.csv` | the 297 curated pairs with annotator labels, for scoring |
| `run_ext.sh` | every step, offline first; model calls marked "needs a go" |
| `score_ext.py` | joins pairs to the pipeline outputs, writes `data/eval_pairs.csv`, prints the tables |
| `data/` (gitignored) | everything above that is data, plus `fetch.log` and `eval_pairs.csv` |
| `data/citation_groups/` | fixer annotator root, seed inputs, edits, revised html |

Pipeline artefacts land in `../experiments_09112026/data/`: `opinions/{cid}.json`,
`inputs/{cid}.tagged.txt` + `.inventory.json`, `runs/g6_ext`, `runs/g6b_ext`, `runs/l_sonnet3_ext`.

## Steps

1. `fetch` — CL html payloads (CL API, read only).
2. `root` → `prepare` → `estimate` — the v8h citation fixer's inputs and a cost estimate, offline.
3. `run` → `check` → `apply` → `export` — the fixer itself (GPT-5.6 on Bedrock, ≈ $0.012 per cluster) and the corrected grouping as revised html.
4. `build` — 0911 inputs on the corrected grouping (`build eyecite` gives a provisional build on CL's own grouping, for sizing before the fixer runs).
5. `gate-prepare` → `gate-run` — two gate passes, on demand (≈ $0.024 per cluster).
6. `label-prepare` → `label-submit` → `label-status` / `label-fetch` — labeler on the union of the two passes, Bedrock batch (≥ 100 records; ≈ $0.05–0.06 per flagged cluster).

Scoring: binary negative on both sides — annotator label ≠ Cited by / not negative; pipeline
applied OR recognized treatment other than Cited by / Affirmed / cert strings. For Kirschenfeld
the granular label is the phrase he judged accurate; for Hellyer the accepted citator code.


## Results

Definitions for the tables below. **Negative** = any treatment other than Cited by, Affirmed by or
a cert string, on either side. **Pipeline** = gate_v6 ×2 → labeler_v3; a pair is negative when the
applied treatment or any recognized treatment is negative. **FLP experts** = any expert in any
benchmark round marked the pair negative. **Annotator** = the external source's own label.
Dahl's pairs come from the 0911 dev/test runs; Kirschenfeld and Hellyer pairs are pending the
labeler batch.

### Across the three sources, binary

| Source | Pairs scored (joined, non-ambiguous) | Annotator negatives among them | Annotator not-negatives among them | Pipeline agrees with annotator (negatives called negative + not-negatives called not negative), of pairs scored | Pipeline recall: annotator negatives also called negative | Pipeline false positives: not-negatives called negative |
|---|---|---|---|---|---|---|
| Dahl | 41 | 41 | 0 | 31 (0.76) | 31 of 41 (0.76) | — |
| GPT-5.6 Luna with web search | 41 | 41 | 0 | 33 (0.80) | 33 of 41 (0.80) | — |
| Kirschenfeld | 33 | 33 | 0 | 26 (0.79) | 26 of 33 (0.79) | — |
| Hellyer | 243 | 232 | 11 | 190 (0.78) | 179 of 232 (0.77) | 0 of 11 |

For Dahl and Kirschenfeld every scored pair is an annotator negative, so agreement and recall are
the same number. For Hellyer agreement also credits the 11 not-negatives the pipeline left alone.
The Dahl dataset ships its own model prediction (a GPT-5.6 call with web search asked for a binary
label); it is a reference row, not part of the pipeline.

Per-source detail (negative counts per source, FLP-expert comparison on Dahl, the citator
reference rows on Hellyer, the 2×2 breakdown, and the granular per-label scores) is in the
Appendix at the end of this file.

### Across the three sources, binary, gate only

Gate-only = a pair counts negative when either gate pass flagged it at medium confidence or above,
or marked it recognized, with no labeler. Same pairs and denominators as the table above.

| Source | Pairs scored (joined, non-ambiguous) | Annotator negatives among them | Annotator not-negatives among them | Gate agrees with annotator (negatives flagged + not-negatives not flagged), of pairs scored | Gate recall: annotator negatives flagged or recognized | Gate false positives: not-negatives flagged or recognized |
|---|---|---|---|---|---|---|
| Dahl | 41 | 41 | 0 | 31 (0.76) | 31 of 41 (0.76) | — |
| GPT-5.6 Luna with web search | 41 | 41 | 0 | 33 (0.80) | 33 of 41 (0.80) | — |
| Kirschenfeld | 33 | 33 | 0 | 30 (0.91) | 30 of 33 (0.91): 25 flagged, 5 recognized | — |
| Hellyer | 243 | 232 | 11 | 212 (0.87) | 209 of 232 (0.90): 187 flagged, 22 recognized | 8 of 11 |

Against the pipeline table above, the gate alone recovers more annotator negatives on
Kirschenfeld (30 vs 26) and Hellyer (209 vs 179) and the same number on Dahl (31 vs 31), but
flags 8 of Hellyer's 11 not-negatives where the full pipeline flags none. The labeler trades
recall for that precision.

Per-pair rows with the join, gate state, pipeline labels and the granular flags are in
`data/eval_pairs.csv` (`score_ext.py`). Labeler batch: 294 records, 0 errors, 4.18M in / 1.39M out
tokens, $16.71 at batch rates.

## Status

2026-10-21: `fetch-cl` 296/296 (all from the API, none cached); `root` (296 clusters, 7,702 CL-linked
authorities); `prepare` 296 fixer inputs, none over 400K chars; `estimate` ≈ $3.60 (5.27M in /
1.85M out tokens); provisional `build eyecite` 296/296 (10,391 groups, 27,174 mentions, 11.0M chars,
780 parallel cites merged); `gate-prepare` g6_ext + g6b_ext: 296 records each, 0 paginated, 4.43M
input tokens per pass (prepare's $1.25/pass ignores reasoning; expect ≈ $3–4 per pass as on 0911).
Fixer run as OpenAI Batch `ext_gpt` (Rachel submitted 14:27, completed ≈14:45): 295/296 ok, all
finish=stop, 5.67M in / 3.54M out tokens ≈ $2.96 at batch rates. Cluster 25044 (Burdine v. Johnson,
ca5 en banc, 280K chars) came back with no JSON object → retried in the same mode as `ext_gpt_redo`
(batch_6ad9352633b4…, submitted 14:52) — same failure (prose, no JSON); not resubmitted. 25044
keeps CourtListener's own grouping. `apply` 295 clusters (one garbled add record in 3017559 dropped,
backup in seed/backups); `export` revised html 296; `build` on the corrected grouping 296/296
(8,937 groups, 27,993 mentions; was 10,391 / 27,174 on CL grouping); `gate-prepare` re-run: g6_ext +
g6b_ext 296 records each, 0 paginated, 4.43M input tokens per pass. Gate SUBMITTED as OpenAI Batch 15:08 / 15:09 (`gate-export` → `gate-submit`): g6_ext_p01
batch_6ad937bd…, g6b_ext_p01 batch_6ad9380a…, 296 requests each, 18 MB each. Both COMPLETED by 16:43 (296/296 each, 0 errors); fetched + parsed. `label-prepare`
l_sonnet3_ext: 294 clusters → 294 records (0 paginated; 487199 and 2469709 skipped: no flags), 3.70M
input tokens, ≈ $11 batch estimate; 1,300 recognized-marked ids routed (969 not otherwise flagged).
Gate coverage of the 297 labelled pairs: 215 join to an inventory id by cited cluster id — of those,
164 flagged ≥ medium, 23 recognized-only, 28 not flagged (negatives: 171 of 191 routed). The other 82
pairs resolve to a different CL duplicate cluster than the one CourtListener's html links to (e.g.
Harvard 8928496 vs public.resource.org 429478 for Balelo), so scoring must join by citation/name as
`goldmap.py` does, not by cluster id alone.
Labeler SUBMITTED as Bedrock batch (`label-submit`, after `aws sso login --profile dev-env`):
job citator-2s-label-l-sonnet3-ext-1792626680 (model-invocation-job/m4vjrgwunt66), 294 records,
input s3://flp-ai-private/Citator/runs/9e61313d-…/twostage/l_sonnet3_ext/input.jsonl.
Next: `label-status` → `label-fetch` → `run_stage.py parse` → score against pairs.csv (join by cited
cluster id, then normalised citation, then case name; binary = applied OR recognized negative).

## Appendix — per-source detail

Full tables behind the Results summary: negative counts per source, the FLP-expert comparison on
Dahl, the citator reference rows on Hellyer, the 2×2 agreement breakdown, and the granular
per-label scores.

### Dahl — 41 SCOTUS pairs, all negative by the annotator

Negative calls per source. Higher is closer to the annotator, who marks every pair negative.

| Source | Pairs scored | Annotator negatives | Called negative | Called not negative | Reading |
|---|---|---|---|---|---|
| Annotator (Dahl) | 41 | 41 | 41 | 0 | by construction |
| Pipeline | 41 | 41 | 31 (0.76 of the 41 annotator negatives) | 10 | 9 applied (5 Overruled, 2 Abrogated, 1 Limited, 1 Questioned) + 22 recognized-only (12 Overruled, 8 Limited, 1 Abrogated, 1 Disapproved) |
| FLP experts, any vote | 41 | 41 | 25 (0.61) | 16 | majority final alone gives 14 |
| Shepard's | 41 | 41 | 29 (0.71) | 12 | reference only; 12 "cite" codes the annotator overrode |

Pairwise agreement on the binary call, out of 41. Higher is better.

| Comparison | Agree | Disagree | Where the disagreement sits |
|---|---|---|---|
| Pipeline vs annotator | 31 | 10 | 10 "law has since changed" citations the pipeline reads as Cited by |
| Pipeline vs FLP experts | 31 | 10 | 8 pipeline-only negatives (recognized overruling/limitation the experts marked Cited by); 2 expert-only negatives the gate missed (MacAllen, Capital Traction) |
| FLP experts vs annotator | 25 | 16 | the 16 are all pairs every expert marked Cited by |
| Shepard's vs annotator | 29 | 12 | reference only |

The pipeline sits between the annotator and the experts: it recovers 31 of the annotator's 41
negatives and agrees with the experts on 31 of 41. Its 8 extra negatives over the experts are all
recognized treatments the opinion reports; its 2 misses against the experts are gate misses
(see the 0911 readme, "Gate patterns to add"). The 8 pairs only the annotator marks negative are
Ex parte Dorr, Ex parte McCardle, Baker v. Grice, Markuson, Chisholm, Philadelphia Co. v. Stimson,
Johnson v. New Jersey and Tehan.

### Kirschenfeld — 33 pairs, all negative by the annotator

These pairs are not in the FLP benchmark, so there is no FLP-expert column. 33 of 33 join to an
inventory id (30 by cited cluster id, 3 by citation or name). Gate-only = a pair counts negative
when either gate pass flagged it at medium confidence or marked it recognized, with no labeler.

| Source | Pairs scored | Annotator negatives | Called negative | Called not negative | Reading |
|---|---|---|---|---|---|
| Annotator (Kirschenfeld) | 33 | 33 | 33 | 0 | by construction: the citators' own negative listings he verified |
| Pipeline | 33 | 33 | 26 (0.79 of the 33 annotator negatives) | 7 | 21 applied + 5 recognized-only; 7 misses: 3 never flagged, 4 flagged then labelled Cited by |
| Gate-only | 33 | 33 | 30 (0.91) | 3 | reference: recall without the labeler's precision step |

Granular, on the 27 pairs where the phrase he judged accurate maps to an FLP label. "Exact" = the
pipeline's label in the same class equals the annotator's; "tier" = the pipeline's most severe label
sits in the same severity tier (Stop / Warning / Caution / Neutral).

| Annotator label | n | Exact | Tier | Predicted instead |
|---|---|---|---|---|
| Questioned by | 10 | 1 | 4 | Cited by 2, Reversed by 2 (recognized), Disapproved by 2 |
| Distinguished by | 8 | 6 | 4 | Cited by 2 |
| Criticized by | 5 | 0 | 4 | Declined to follow by 3, Distinguished by 1, Disapproved by 1 |
| Overruled by | 2 | 0 | 1 | Abrogated by 1, Declined to follow by 1 |
| Declined to follow by | 2 | 0 | 1 | Cited by 1, Criticized by 1 |
| **All** | **27** | **7 (0.26)** | **14 (0.52)** | |


Per-label scores on base labels, same definitions as for Hellyer.

| Label | Annotator n | Pipeline n | Recall | Precision | F1 |
|---|---|---|---|---|---|
| Questioned by | 10 | 1 | 0.10 | 1.00 | 0.18 |
| Distinguished by | 8 | 6 | 0.50 | 0.67 | 0.57 |
| Criticized by | 5 | 1 | 0.00 | 0.00 | 0.00 |
| Overruled by | 2 | 1 | 0.00 | 0.00 | 0.00 |
| Declined to follow by | 2 | 4 | 0.00 | 0.00 | 0.00 |
| **All** | **27** | | **accuracy 0.19 · macro-F1 0.15 · tier accuracy 0.52** | | |

Exact agreement is low because KeyCite and Shepard's phrases (Questioned, Criticized) rarely map
one-to-one onto the pipeline's labels; half of the pairs land in the same severity tier. The
sample is too small for a per-label claim beyond Distinguished by (6 of 8 exact).

### Hellyer — 264 pairs: 232 negative, 11 not negative, 17 ambiguous by the annotator

260 of 264 join (185 by cited cluster id, 75 by citation or name); the 4 unjoined are later
opinions in the same litigation whose html does not link the cited case. Binary rows exclude the
17 ambiguous pairs (243 pairs). The three citators are reference rows: a citator counts negative
when it applied any negative code, and the annotator's label was formed by judging those codes.

Denominators: **pairs scored** = joined pairs minus the 17 ambiguous = 243; **annotator negatives** =
the 232 of those 243 he marked negative; **annotator not-negatives** = the other 11.

| Source | Pairs scored | Annotator negatives | Agrees with annotator: negatives called negative + not-negatives called not negative, of 243 | Recall: annotator negatives also called negative, of 232 | False positives: annotator not-negatives called negative, of 11 |
|---|---|---|---|---|---|
| Pipeline | 243 | 232 | 190 (0.78) | 179 (0.77) | 0 |
| Gate-only | 243 | 232 | 212 (0.87) | 209 (0.90) | 8 |
| Shepard's | 243 | 232 | 180 (0.74) | 175 (0.75) | 6 |
| KeyCite | 243 | 232 | 161 (0.66) | 157 (0.68) | 7 |
| BCite | 243 | 232 | 87 (0.36) | 78 (0.34) | 2 |

How the pipeline's 190 agreements and 179 recalled negatives relate:

| Annotator (Hellyer) says | Pairs | Pipeline says negative | Pipeline says not negative |
|---|---|---|---|
| negative | 232 | **179** (agree, counted in recall) | 53 (missed) |
| not negative | 11 | 0 (false positive) | **11** (agree) |
| total scored | 243 | 179 | 64 → 190 agree (179 + 11) |
| ambiguous, not scored | 17 | 3 | 14 |

The pipeline agrees with the annotator more often than any citator, with no false positives on
the 11 pairs he judged not negative. Its 53 misses split: 23 the gate never flagged, 30 the gate
flagged and the labeler returned to Cited by. On the 17 ambiguous pairs the pipeline marks 3
negative. The 6 ACAN pairs ("among conflicting authorities", no FLP equivalent) are all Cited by.

Granular, on the 226 pairs with an FLP label (the accepted citator code, crosswalked).

| Annotator label | n | Exact | Tier | Predicted instead |
|---|---|---|---|---|
| Distinguished by | 124 | 102 | 101 | Cited by 21, Modified by 1 |
| Criticized by | 31 | 2 | 17 | Declined to follow by 12, Cited by 9, Disapproved by 4 |
| Declined to follow by | 23 | 5 | 16 | Distinguished by 9, Cited by 6 |
| Overruled as recognized by | 19 | 15 | 18 | Cited by 1, Abrogated by 1, Reversed by 1 |
| Questioned by | 12 | 4 | 5 | Criticized by 2, Distinguished by 2, Limited by 1 |
| Declined to follow as recognized by | 6 | 0 | 1 | Cited by 4, Distinguished by 1, Disapproved by 1 |
| Overruled by | 5 | 2 | 2 | Disapproved by 2, Cited by 1 |
| Abrogated as recognized by | 3 | 0 | 0 | Cited by 3 |
| other (Reversed, Disapproved/Limited as recognized) | 3 | 0 | 1 | Cited by 2, Limited by 1 |
| **All** | **226** | **130 (0.58)** | **161 (0.71)** | |


Per-label scores on base labels (recognized labels compared by their base, e.g. "Overruled as
recognized by" = Overruled by; prediction = the pipeline's most severe label, applied or
recognized). Recall = annotator pairs with the label that the pipeline also gave it; precision =
pipeline pairs with the label that the annotator also gave it.

| Label | Annotator n | Pipeline n | Recall | Precision | F1 |
|---|---|---|---|---|---|
| Distinguished by | 124 | 115 | 0.81 | 0.87 | 0.84 |
| Criticized by | 31 | 5 | 0.06 | 0.40 | 0.11 |
| Declined to follow by | 29 | 19 | 0.21 | 0.32 | 0.25 |
| Overruled by (19 recognized + 5 applied) | 24 | 15 | 0.62 | 1.00 | 0.77 |
| Questioned by | 12 | 10 | 0.42 | 0.50 | 0.45 |
| Abrogated by, Reversed by, Disapproved by, Limited by | 6 | 14 | 0.17 | — | — |
| Cited by (pipeline only) | 0 | 47 | — | — | — |
| **All** | **226** | | **accuracy 0.57 · macro-F1 0.34 · tier accuracy 0.71** | | |

Severity tiers: Stop (Overruled, Abrogated, Reversed) recall 0.62 / precision 0.89 on 40 pairs;
Caution (Distinguished, Criticized, Declined to follow) recall 0.73 / precision 0.96 on 184.

Distinguished by, the bulk of the set, lands exactly 102 of 124 times and its misses are almost all
Cited by. Criticized by is the weak label: the pipeline calls it Declined to follow by 12 times and
Cited by 9 times. Recognized overrulings are found 15 of 19 times; the smaller recognized classes
(Declined to follow, Abrogated) are mostly returned as Cited by.

