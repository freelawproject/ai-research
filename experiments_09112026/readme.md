# 0911 — Two-stage citator: cheap gate → strong labeler

**Question.** Can a cheap model decide, per cited case, whether the citing opinion does anything
more than merely cite it, so that a strong model labels only the flagged cases — and does the pair
beat the single-stage 0529 Sonnet run on the reviewed benchmark at lower cost?

**Answer (test set, 233 opinions, run 2026-09-14): yes.** Two GPT-5.6 gate passes unioned, then
Sonnet 4.6 with `labeler_v2`, beats 0529 on every axis at 57% of its cost. See *Recommendation*.

Scope: citing-reference treatments only. Direct history (the case on appeal and affirm / reverse /
vacate / remand / cert relationships) is a docket fact routed structurally elsewhere; "as recognized
by" treatments (4% expert agreement) are excluded from both stages and from scoring.

## Recommendation

**Adopt: gate_v4 × 2 passes (GPT-5.6 Luna, reasoning effort high, flags unioned at ≥ medium
confidence) → Sonnet 4.6 with `prompts/labeler_v2.md`.**

| test, 6,710 pairs, as labeled | accuracy | positives recovered | false flags on Cited-by pairs | Distinguished P / R | macro F1 (3 classes) | $/opinion (batch) |
|---|---|---|---|---|---|---|
| **gate → Sonnet 4.6 / labeler_v2** | **0.969** | 0.61 | **1.7%** | **0.59 / 0.66** | **0.559** | **$0.053** |
| gate → Opus 5 / labeler_v1 (recall configuration) | 0.930 | **0.81** | 6.4% | 0.36 / **0.79** | 0.632 | $0.086 |
| 0529 Sonnet single-stage | 0.940 | 0.59 | 4.5% | 0.54 / 0.58 | 0.533 | $0.093 |

Keep **gate → Opus 5 / labeler_v1** as the recall configuration for a human-reviewed queue: 81% of
positives (Distinguished 0.79, Criticized 0.79, Overruled 5 of 7) but 6.4% of Cited-by pairs flagged.
Opus / labeler_v2 sits between (0.71 recovered at 0529's false-flag rate) but its stricter Criticized
definition loses the dissent-side Criticized cases (see *Findings* 9).

Conditions: (1) direct history must ship via the structural route (appellate-chain linker +
disposition) — 0529 handled it in the same call; (2) the citation-correction step (GPT v8g fixer
or the extraction encoder) must run in front of the gate — all numbers assume corrected grouping;
(3) decide the separate-opinion convention in the annotation guidelines (*Findings* 9); (4) both
gate passes are load-bearing — single-pass recall varies 0.76–0.80; (5) the test set has been
touched twice (labeler v1, v2); further iteration stays on dev.

## Design

| | Stage 1 — gate | Stage 2 — labeler |
|---|---|---|
| Question | For every cited case: merely cited, or more? | For every flagged case: which exact treatment? |
| Models tried | Kimi K2.5 (dropped), **GPT-5.6 Luna** via Bedrock Converse | **Sonnet 4.6**, Opus 5 (thinking on, tool output) |
| Frozen prompt | `prompts/gate_v4.md` | `prompts/labeler_v2.md` |
| Input | `<citingCase/>` + `<citedCaseInventory>` (id, name, citations, mention count, contrast-word `signals`) + opinion with every mention tagged `<citedCase group="N">` | `<citingCase/>` + `<flaggedCases>` + the whole opinion with flagged mentions tagged `<flaggedCase id="N">`, other citation tags stripped |
| Output | one verdict per inventory id: `{id, more, confidence, quote, signal}`; `onAppeal` ids; an opinion with no flags is skipped | one entry per flagged id: treatment ∈ 8 Citing Reference labels ∪ {Cited by, Other treatment}, opinionType (exact Lead / Concurrence / Dissent), quote, rationale |

**Inputs.** 382 benchmark citing clusters, split 149 dev / 233 test
(`experiments_09022026/triage/inputs/splits_full.csv`). Text = CourtListener `html_with_citations`,
replaced by centralia's PDF reading where the 0903 pass returned `valid` and it covers ≥ 85% of the
CL text (88 clusters). Citation grouping = corrected: verified gold `revised_html` for the 99
clusters of the triage small split, the GPT-5.6 v8g citation fixer (`seed_citations.sh`, 6,814 edits,
$5.23) for the other 283; cluster 112786 was content-filtered twice and stays on eyecite grouping.
Gold = `citator-benchmark/data/treatments_most_negative.csv` (majority final + up to three expert
votes), joined to inventory ids by cited cluster id, then normalized citation, then case name:
99.5% of in-scope dev pairs (152/152 positives), 99.1% of test (225/228). Opinions over 600K
chars are paginated as in 0529 and merged (gate: union, best confidence; labeler: most severe).

## Metrics (`score.py`, `compare.py`)

Scope = gold pairs with a final, minus direct-history pairs (final or any vote) and "as recognized
by" finals. Two truths: **majority** (the benchmark final) and **any expert** (a prediction counts
if any expert voted it). Gate: precision / recall at each confidence threshold, fraction of pairs
flagged, opinions skipped and positives lost in them. Labeler: exact and any-expert agreement on
flagged pairs, share of gate false positives returned as Cited by, per-class P/R. End to end
(unflagged = Cited by): accuracy, false-flag rate on Cited-by pairs, per-class P/R, macro F1 over
classes with ≥ 20 examples (2 on dev, 3 on test — not comparable to the 6-class 0902 figures), next
to the 0529 predictions on the same pairs. `--dissent-as-cited` scores the alternative convention
(a treatment whose only source is a concurrence or dissent counts as Cited by) for pipeline and
baseline alike. Cost per stage from recorded token usage; batch = half of on-demand where offered.

## Results

### Gate (dev: 149 clusters, 3,699 pairs, 152 positives; test: 233 clusters, 6,710 pairs, 225 positives)

| gate | threshold | recall (majority) | precision | recall (any expert) | pairs flagged | positives lost by skipping opinions | cost |
|---|---|---|---|---|---|---|---|
| dev: Kimi K2.5, v1 | all | 0.579 | 0.218 | 0.417 | 10.9% | 0 / 31 skipped | $1.35 |
| dev: Kimi K2.5, v2 | all | 0.520 | 0.336 | 0.363 | 6.4% | 15 | $1.51 |
| dev: GPT-5.6, v1, medium effort | all | 0.645 | 0.394 | 0.444 | 6.7% | 3 | $0.61 |
| dev: GPT-5.6, v2, high | ≥ medium | 0.809 | 0.361 | 0.580 | 9.2% | 2 | $1.20 |
| dev: GPT-5.6, v3, high | ≥ medium | 0.796 | 0.369 | 0.546 | 8.9% | 0 | $1.31 |
| dev: GPT-5.6, v3, xhigh | ≥ medium | 0.757 | 0.363 | 0.512 | 8.6% | 0 | $2.19 |
| dev: GPT-5.6, **v4**, high — pass 1 / pass 2 | ≥ medium | 0.763 / 0.789 | 0.377 / 0.401 | 0.525 / 0.536 | 8.3% / 8.1% | 4 / 0 | $1.28 / $1.31 |
| dev: **v4 ∪ v4** | ≥ medium | **0.821** | 0.355 | 0.571 | 9.5% | 0 | ≈ $2.6 |
| dev: v4 ∩ v4 | ≥ medium | 0.728 | 0.430 | 0.486 | 6.9% | | |
| dev: cross-prompt unions (v2∪v4, v3∪v4, v2∪v3) | ≥ medium | 0.828–0.848 | — | 0.59–0.62 | 9.7–10.6% | | |
| **test: v4 pass 1 / pass 2** | ≥ medium | 0.756 / 0.804 | 0.28 | — | 9.0% / 9.5% | | $2.17 / $2.12 (38 min each) |
| **test: v4 ∪ v4** | ≥ medium | **0.818** (184/225) | 0.257 | 0.501 | 10.7% | 1 / 69 skipped | ≈ $4.3 |
| test: 0529 Sonnet single-stage, same pairs | — | 0.591 | — | 0.402 | 6.4% | | |

Prompt versions: v1 = flagged list only; v2 = one verdict per inventory id + contrasted-string-cite
/ qualified-survey rule; v3 = + mechanical `signals` (contrast words within 250 chars of a mention,
38% of entries) + four patterns (cases a party / dissent / court below relied on that the court
sets aside; precedents set against this case; conflicting decisions the court rules against; "cf.");
v4 = v3 with dev-lifted phrasing rewritten as general conventions (every quoted phrase checked
against dev and test texts).

### Labeler (dev flags = v4 ∪ v4 ≥ medium: 350 pairs in 103 opinions, 125 positives + 225 Cited-by; test: 715 pairs in 164 opinions, 184 + 531)

| labeler | prompt | split | exact on gold positives | any expert | gate false positives returned as Cited by | cost (on-demand) |
|---|---|---|---|---|---|---|
| Opus 5 | oracle flags (152 gold positives) | dev | 0.789 | 0.914 | — | $8.07 |
| Sonnet 4.6 | oracle flags | dev | 0.691 | 0.914 | — | $4.44 |
| Opus 5 | v1 | dev | 0.856 | 0.896 | 0.324 | $17.13 |
| Opus 5 | v2 | dev | 0.824 | 0.904 | 0.462 | $18.78 |
| Sonnet 4.6 | v1 | dev | 0.704 | 0.896 | 0.609 | $9.08 |
| Sonnet 4.6 | v2 | dev | 0.712 | 0.888 | 0.756 | $9.22 |
| Opus 5 | v1 | test | 0.924 | 0.940 | 0.217 | $31.67 |
| Opus 5 | v2 | test | 0.793 | 0.848 | 0.452 | $33.89 |
| Sonnet 4.6 | v1 | test | 0.728 | 0.843 | 0.646 | $15.92 |
| Sonnet 4.6 | v2 | test | 0.696 | 0.815 | 0.791 | $16.21 |

`labeler_v2` = v1 + base rate stated (most flagged cases are merely cited), evidence requirement (a
non-Cited-by label needs a quoted passage where the court itself acts on the case) with five
look-alikes that are not treatment, Criticized narrowed to fault with that case's own reasoning,
Distinguished as a two-part test (difference identified *and* consequence drawn), one mandatory
entry per flagged id, exact Concurrence / Dissent opinion types.

### End to end (unflagged = Cited by)

| pipeline | split | accuracy | positives recovered | false flags on Cited-by | Distinguished P / R | Criticized P / R | macro F1 | $/opinion on-demand / batch |
|---|---|---|---|---|---|---|---|---|
| gate → Opus v1 | dev | 0.947 | 0.82 | 4.3% | 0.58 / 0.76 | — | 0.817 (2) | $0.132 / $0.075 |
| gate → Opus v2 | dev | 0.954 | 0.79 | 3.4% | 0.60 / 0.73 | — | 0.817 (2) | $0.143 / $0.080 |
| gate → Sonnet v1 | dev | 0.959 | 0.64 | 2.5% | 0.65 / 0.61 | — | 0.806 (2) | $0.087 / $0.052 |
| gate → Sonnet v2 | dev | 0.968 | 0.64 | 1.6% | 0.66 / 0.61 | — | 0.809 (2) | $0.079 / $0.048 |
| 0529 Sonnet | dev | 0.947 | 0.62 | 3.5% | 0.63 / 0.58 | — | 0.789 (2) | ≈ $0.186 / $0.093 |
| gate → Opus v1 | test | 0.930 | 0.81 | 6.4% | 0.36 / 0.79 | 0.31 / 0.79 | 0.632 (3) | $0.154 / $0.086 |
| gate → Opus v2 | test | 0.945 | 0.71 | 4.5% | 0.42 / 0.75 | 0.03 / 0.04 | 0.514 (3) | $0.164 / $0.091 |
| gate → Sonnet v1 | test | 0.958 | 0.64 | 2.9% | 0.47 / 0.68 | 0.14 / 0.04 | 0.534 (3) | $0.087 / $0.053 |
| **gate → Sonnet v2** | test | **0.969** | 0.61 | **1.7%** | **0.59 / 0.66** | 0.20 / 0.04 | **0.559 (3)** | $0.088 / **$0.053** |
| 0529 Sonnet | test | 0.940 | 0.59 | 4.5% | 0.54 / 0.58 | 0.25 / 0.04 | 0.533 (3) | ≈ $0.186 / $0.093 |

Under the dissent-as-Cited convention (test): Sonnet v2 0.971 / 1.5% / Distinguished 0.61–0.65;
Opus v2 0.959 / 2.9% / 0.51–0.70; 0529 0.958 / 2.6% / 0.57–0.57; Criticized = 0 for every row.
Cost: the gate is $0.018/opinion on-demand ($0.008 via OpenAI Batch); the labeler runs on the
~70% of opinions with a flag and is 85% of the bill. Project spend ≈ $180.

## Findings

1. **Gate model.** GPT-5.6 beats Kimi K2.5 on both axes at every prompt; Kimi turned *more*
   conservative when forced to rule on every id. Reasoning effort is GPT's largest lever (medium →
   high: 0.61 → 0.76–0.81); xhigh is worse, twice the output tokens, and exhausted a 32K output
   cap on the two largest opinions (cap now 64K).
2. **Gate prompt.** Per-id verdicts removed silent omission; the string-cite / reliance rules
   recover exactly the cases they describe (Blaisdell's survey, United Haulers' six local-processing
   cases, NFIB's Necessary-and-Proper precedents) but the net effect sits inside run-to-run noise
   (any two runs differ on ~10 positives each way).
3. **Union of passes is the reliable recall lever.** Two v4 passes agree on 73% of their flags
   (Jaccard); 14 positives are caught by exactly one → 0.76 / 0.79 single → 0.82 union at +1.3
   pts flagged. Test reproduced dev exactly (0.82 at 10.7%).
4. **Gate floor.** 27 of 152 dev positives are missed by both passes, almost all 2-of-3 labels (2 of
   57 unanimous positives missed): a footnote string of contrary state decisions (103307, 9 pairs)
   and *Hanna v. Plumer* in *Walker v. Armco*, where the Court applies Hanna's own test and finds
   no direct collision. Skipping whole opinions costs almost nothing (0–1 positives).
5. **Sampling and fine-tuning.** No temperature is set: Bedrock rejects `temperature`/`topP` for
   GPT-5.6 and caps temperature at 1.0; OpenAI accepts it only at effort *none*. Pass diversity comes
   from default sampling. No fine-tuning path exists for GPT-5.6 (OpenAI) or Kimi on Bedrock
   (Bedrock SFT: Nova / Llama 3.x / Claude 3 Haiku; RFT: gpt-oss-20b, Qwen3-32B); a trained gate
   would be RFT on an open-weight model or the CaseLawModernBERT triage encoder seeded by these flags.
6. **Labeling true positives is solved; filtering is the work.** Given a real positive, Opus names
   the exact majority label 0.82–0.92 of the time (Sonnet 0.70–0.73) and matches at least one expert
   0.85–0.94. With labeler_v1 Opus returned Cited by on only 22–32% of the gate's false positives;
   v2 raised that to 45–46% (Sonnet 61–65% → 76–79%) at a ~3-point cost in positive recall for
   Opus and none for Sonnet.
7. **What the false flags are.** Opus v1's dev relabels: 29% are treatments found only in a
   dissent or concurrence (vs 4 of 152 gold positives living solely there); 34 of 44 "Criticized"
   were unanimous Cited-by pairs where the court calls a group of decisions unpersuasive; 113 of 152
   had only two expert votes. Sonnet's remaining errors are "difference described, no consequence
   drawn" (*Baldwin v. Fish & Game* in *Reeves*).
8. **Rare classes** (Overruled / Abrogated / Limited / Questioned) rest on < 5 examples per split;
   the labeler's severity tier is usually right when the label is not (three gold Overruled → Abrogated).
9. **The separate-opinion convention is unsettled in the gold.** Mapping dissent-only treatments to
   Cited by removes ~29% of Opus's false flags on dev and cuts the test false-flag rate (6.4% → 4.0%),
   but erases the Criticized class on test: 18 of the 19 gold Criticized positives are dissent
   treatments, 15 in one opinion (110077) where both experts credited the dissent. Decide it in the
   guidelines, then either record such treatments as Cited by or stop scoring them as errors.
10. **Parser note.** Sonnet (and any run parsed before 2026-09-14 evening) sometimes returns the
    `citedCases` array as a JSON string inside the tool call; `records._as_list` decodes it. Early
    "dropped ids" attributed to Sonnet were this bug; all runs were re-parsed.

## 0529 single-stage Sonnet vs the gated pipeline

Reference = gate_v4 × 2 → labeler_v2; Opus 5 is the recall variant, Sonnet 4.6 the precision
variant. Dev numbers unless marked test; examples are real dev pairs.

| | 0529 single-stage Sonnet 4.6 | Gated pipeline (gate → labeler_v2) |
|---|---|---|
| **Recall of treatments** | 62% of positives; Distinguished 0.58; Criticized ≈ 0 (test 1 of 24); Abrogated 0 of 17 on the 0410 eval. Misses whole groups a court contrasts with its holding: Blaisdell's survey (*Green v. Biddle*, *Walker v. Whitehead*, *Penniman's Case*), United Haulers' six local-processing cases (*Foster-Fountain*, *Johnson v. Haydel*, *Toomer*), NFIB's Necessary-and-Proper precedents (*Raich*, *Comstock*, *Sabri*, *Jinks*). | Opus variant 79% (Distinguished 0.73; test 0.79, Criticized 0.79 with v1); recovers 29 dev positives 0529 misses, including every case above. Sonnet variant 0.61–0.68. Gate floor ≈ 18% of positives, mostly 2-of-3 labels: the 103307 footnote string; *Hanna* in *Walker v. Armco* (0529 catches it, the gate never has). |
| **False flags on Cited-by pairs** | 3.5% (test 4.5%). *Category* errors the taxonomy invites: 29 of 125 dev false flags are "Affirmed by" and 22 "Cert. denied by" on cases not on appeal (*Steelman v. Wheaton*, *Reddy v. Litton*), plus 15 spurious "Overruled by". | Opus v2 3.4%; Sonnet v2 1.6% (test 1.7%). *Degree* errors on real contrasts: a difference described while relying on the case (*Baldwin v. Fish & Game* in *Reeves*), and separate-opinion treatments the experts did not count (*Louisiana Nat'l Bank v. Hebert*, *Overmier v. Traylor*: only the dissent finds fault). |
| **Exact label on positives found** | 0.53 of positives exactly right; drifts toward Distinguished: *Medcenters*, *Caudill*, *Blue Cross of Illinois* (gold Overruled) → Distinguished; *Mussry* (Abrogated) → Limited. | Opus 0.82 (Sonnet 0.71); agrees with ≥ 1 expert 0.90. Severity tier usually right even when the label is not: the three Overruled → Abrogated (Stop), *Mussry* → Abrogated ✓. Rare classes noisy: *Hawkins* (Limited) → Abrogated, *Troppi* (Limited) → Disapproved. |
| **Direct history** | Handled in the same call (QWK 0.94 on the 0529 eval) — its strongest area. | Out of scope by design; must come from the structural route. |
| **Coverage / extraction** | Extracts its own citations; no prediction for 8% of dev pairs; joins by citation string (the Proffitt/Teterud mis-join class). | Needs a coreference-corrected inventory (GPT fixer or extraction encoder); 99% of pairs joined by cluster id; inherits that step's misses (0.4% of positives). |
| **Cost / opinion (batch)** | $0.093 (≈ $0.186 on-demand). | Opus ≈ $0.086, Sonnet ≈ $0.053 (on-demand $0.164 / $0.088); the gate skips ~30% of opinions and the labeler emits 3–5 entries instead of one per cited case. |
| **Latency / ops** | One Bedrock batch job, one prompt, one model. | Two GPT passes (~38 min per 233 opinions at 6 workers) then a labeler pass; two vendors, two prompts, a corrected-grouping preprocessing step; every stage resumable and logged. |
| **Stability** | One run; run-to-run variance not measured. | Single-pass gate recall varies ±6–10 positives; the two-pass union is part of the design. |

**What needs improvement.** (1) Gate floor: a third pass or second prompt variant (+3–5 pts recall
on dev for +$0.008) or a trained gate. (2) Labeler precision on "difference described, no
consequence drawn" — a worked negative example or a two-step ask (quote the consequence sentence,
then label). (3) The separate-opinion convention (*Findings* 9). (4) Rare classes: report
severity-tier agreement as the headline until the benchmark grows. (5) Wire direct history back in
and evaluate both halves against the 0529 full-taxonomy number. (6) Production routing: gate via
OpenAI Batch, labeler via Bedrock batch. (7) The extraction-correction step is a hard dependency.

## Run

Depends on sibling code in this repo: `experiments_09022026/triage/` (`tagged_text.py`,
`feed_centralia.py`, `citation_seed.py`, `openai_batch.sh`, prompts) for the input conversion and
the citation fixer, and `citator-pipeline/utils/` (`batch_utils.py`, `bedrock_converse_utils.py`,
`preprocess.py`) for Bedrock batch/S3 plumbing and pagination. Gold comes from
`citator-benchmark/data/` (separate, non-git folder).

Environment: `uv` via `run.sh` (boto3, json-repair, requests, eyecite), the `dev-env` SSO profile;
`CITATOR_S3_BUCKET` / `CITATOR_BATCH_ROLE_ARN` for Bedrock batch; CourtListener token from
`citator-benchmark/.env`; `OPENAI_KEY` (env or `./.env`) only for the citation fixer.

```bash
bash run.sh build_inputs.py fetch                    # 382 CL payloads -> data/opinions/ (viewer caches first, API for the rest)
bash run.sh build_inputs.py centralia                # valid 0903 centralia readings + coverage guard -> data/centralia_feed.json
bash seed_citations.sh root|centralia|prepare|run|check|redo|apply|export   # GPT v8g citation fix for the 283 non-gold clusters
bash run.sh build_inputs.py build                    # -> data/inputs/{cid}.tagged.txt + .inventory.json (corrected grouping)
bash run.sh build_inputs.py show --cid 1722 --stage label --flag 3,7        # eyeball a stage input

# frozen gate: two passes, high effort, signals
bash run.sh run_stage.py prepare --stage gate --name g4_gpt_test  --model gpt --split test --prompt prompts/gate_v4.md --effort high --signals
bash run.sh run_stage.py prepare --stage gate --name g4b_gpt_test --model gpt --split test --prompt prompts/gate_v4.md --effort high --signals
bash run.sh run_stage.py run --name g4_gpt_test --workers 6      # Converse on-demand (GPT has no Bedrock batch)
bash run.sh run_stage.py run --name g4b_gpt_test --workers 6

# labeler on the union of the two passes
bash run.sh run_stage.py prepare --stage label --name l_sonnet2_test --model sonnet --split test \
     --from-gate g4_gpt_test,g4b_gpt_test --min-confidence medium --prompt prompts/labeler_v2.md
bash run.sh run_stage.py run --name l_sonnet2_test --workers 3   # or `submit` / `fetch` for Bedrock batch when >= 100 records

bash run.sh score.py   --label l_sonnet2_test --gate g4_gpt_test,g4b_gpt_test [--dissent-as-cited]
bash run.sh compare.py --gate g4_gpt_test,g4b_gpt_test --labels l_opus_test l_opus2_test l_sonnet_test l_sonnet2_test
```

`prepare` prints record count, size and a rough cost; `--ids …` limits a run to named clusters,
`run --limit N` runs N records; `--from-gold` builds an oracle labeler run from the gold positives;
`fill` re-queries ids a labeler omitted; `status` lists runs. Model notes: Opus 5 rejects sampling
parameters and thinks adaptively; Sonnet 4.6 runs with an 8K thinking budget; with thinking on the
tool is offered with `tool_choice: auto` and the parser falls back to JSON in the text. GPT-5.6 on
Bedrock: Converse only, `additionalModelRequestFields = {"reasoning": {"effort": …}}`, no
temperature. Kimi: no tool use, 16K output cap, prompt prepended to the user turn. Opus 5 Bedrock
price unverified (Opus 4.5 tier assumed).

## Layout

| file | role |
|---|---|
| `common.py` | paths, treatment lists, model table + prices, gold loaders, citation normalizer, name tokens |
| `prompts/` | `gate_v1…v4.md` (v4 frozen), `labeler_v1.md`, `labeler_v2.md` (recommended) |
| `build_inputs.py` | CourtListener fetch, centralia feed + coverage guard, tagged text + inventory (revised html or eyecite), `signals`, stage input rendering |
| `bench_ids.py` | id lists: `seed` (283 to correct), `gold` (99 verified), `centralia` (seed ∩ coverage guard) |
| `seed_citations.sh`, `export_revised_html.py` | GPT citation-fix pass through the triage harness + server-side revised-html export |
| `goldmap.py` | gold pair → inventory id (cluster id, normalized citation, case name) |
| `records.py` | Bedrock bodies per model family, tool schemas, output parsing / normalization |
| `run_stage.py` | prepare / submit / status / fetch / run / fill / parse for a named run |
| `score.py`, `compare.py` | metrics per run (`score.json`, `pairs.csv`); pipeline comparison table |
| `data/` (gitignored) | `opinions/`, `opinions_centralia/`, `annotator_bench/`, `citation_seed/`, `inputs/`, `runs/<name>/` |
