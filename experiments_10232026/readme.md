# 1023 — Clairic (System One extraction model) as a zero-shot citation extractor

Tests whether Cloudflare's Clairic, the first "System One" extraction model, can extract
case-citation mentions from opinion text without training, and whether per-case fields can do
coreference. Scored against the 49-cluster gold_dev set with the triage scorer, so results land as
rows next to eyecite, the encoder and the LLM correction passes in
`../experiments_09022026/model_comparison_dev.md`.

Reference rows on gold_dev (mention F1 / coref F1): stored CourtListener markup 0.857 / 0.975;
published encoder r4 + Id. rule 0.973 / 0.985; GPT-6 Sol v8g 0.986 / 0.993; Opus 5 0.993 / 0.993.
Bar for this experiment: beat the stored-markup floor on extraction; matching the trained encoder
zero-shot is not expected. The likelier payoffs are a cheap second extractor for disagreement
adjudication (`../experiments_09022026/triage/analysis/disagreement_dev.py`) and a coreference
experiment that needs no training.

## The model (vendor-reported, 2026-10-22)

| | Clairic |
|---|---|
| Id, access | `@cf/cloudflare/clairic` on Workers AI, preview since 2026-10-19, provided "AS IS"; open weights promised, no date. REST `POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/@cf/cloudflare/clairic`, bearer token with Workers AI Read + Edit |
| Base model, size, architecture | Undisclosed. One parallel forward pass over the input, no generated tokens, calibrated probabilities |
| Context | 65,536 tokens. Schema: "Fields that do not fit next to the state in one 8,192-token pass are split across parallel passes." Whether a state over 8,192 tokens is truncated is undocumented; the response carries `truncated`, `usage.passes`, `usage.state_tokens` |
| Input | `state` text (+ up to 4 images), `fields` map with no count limit, per-field `type` (text, number, date, time, datetime, duration, list, estimate, forecast), `items` for lists (text, number, date), `instructions`, `null_threshold`; `options.return_state_text` |
| Output | Per field: `value`, `span`/`spans` (character offsets, basis undocumented), `guess`, `p_null`, `confidence`. A list has one confidence for the whole list and no per-item probability. No coreference or grouping primitive |
| Price | $0.10 per M input tokens, no output charge. Whether billed tokens multiply by `passes` is undocumented |
| Not documented | Rate limits, batch API, list-length cap, tokenizer. No independent benchmark on long documents |

The class has one member. Adjacent non-generative extractors: GLiNER2.5 (Fastino, open encoder,
74M–287M, spans with offsets, no coref), GLiFormer-large (Knowledgator, DeBERTa 575M, NER +
relations + structuring), NuExtract3 (Qwen3.5-4B, generative JSON, no offsets). Decision models
(Jev, Clef, mammoth-1; `../experiments_10222026`) return probabilities over listed options and
cannot extract.

## Inputs (step 1, built 2026-10-22)

`bash run.sh build_inputs.py` → `data/inputs/{cid}.json` + `index.csv`. Text is the annotator's
gold frame (`State(cid, seed_state=False).texts`, the frame the scorer anchors in), one record per
opinion. 49 clusters, 68 opinions, 2,953 gold mentions, 343,931 o200k tokens; per opinion median
3,912 tokens, p90 9,519, max 27,135 (809122's dissent, 124K chars). 9 opinions exceed one
8,192-token pass, none exceeds the context. The inventory for D3 is the 0911 cited-case inventory
reduced to the case name plus one reporter citation (both available from CourtListener metadata;
the inventory's mention strings are never sent): median 16 cases per cluster, max 141.

## Field designs (`prompts/fields_d{1,2,3}.json`)

| design | fields per request | what it tests | scored on |
|---|---|---|---|
| D1 | one `list` of `text`: every case-citation mention, convention in the instructions | can one field carry the span convention (eyecite-shaped core spans with pincite, parallels separate, Id./supra, name-only references; statutes etc. excluded) | mention F1 (coref row meaningless: singleton groups) |
| D2 | three lists: reporter citations / Id. + supra / name-only references | does splitting the convention by mention kind help recall on the classes stored markup misses (name-only 215 of dev misses) | mention F1 |
| D3 | one list per inventory case: "every reference to {name}, cited as {citation}", ≤ 60 fields per request | coreference by extraction; a request whose fields need a second pass over a long state is refused, so the inventory is split across requests | mention + coref F1 |
| D4 | D1 spans → published r4 linker + Id. rule | extraction swap only | mention + coref F1 — **not wired yet** |

Whole dev set per design ≈ 0.38M state tokens at 4 chars/token → ≈ $0.04 per pass; D3 averages
32 fields per request, so its cost depends on how many passes the server uses.

## Steps and commands

```bash
bash run.sh build_inputs.py                                                   # step 1 (done)
bash run.sh make_requests.py --design d1                                      # step 2: data/requests/d1.jsonl, 68 requests
bash run.sh make_requests.py --design d3 --max-fields 60                      # D3: ≤ 60 case fields per request (one pass)
bash run.sh run_clairic.py run --name d1 --requests data/requests/d1.jsonl --dry-run   # prints the first body, sends nothing
bash run.sh run_clairic.py run --name d1 --requests data/requests/d1.jsonl --limit 2   # smoke — needs a go
bash run.sh run_clairic.py run --name d1 --requests data/requests/d1.jsonl             # full — needs a go
bash run.sh run_clairic.py status --name d1
bash run.sh to_predictions.py --run d1                                        # data/pred_d1.json + usage / truncated / passes / offset-basis report
python3 ../experiments_09022026/triage/analysis/score_encoder.py --pred data/pred_d1.json \
    --ids $(cat ../experiments_09022026/triage/data/citation_seed/dev_ids.txt) --label "clairic d1"
```

`run_clairic.py` needs `CF_ACCOUNT_ID` and `CF_API_TOKEN` (the `CLOUDFLARE_ACCOUNT_ID` / `CLOUDFLARE_API_TOKEN`
names work too) in `.env` in this folder, gitignored and loaded by `run.sh`; it refuses to send without both; it resumes by request key, retries 429/5xx, and
stores every answer with its usage. Sent in all (2026-10-23): smoke, D1, D2, D3 smoke, D3 = $0.224.

**Smoke first** (done, see below): the open questions were truncation past 8,192 tokens, passes
and billing on a long state, the offset basis of `spans`, and list length on a 200-mention opinion.
A D3 request with 141 fields over a 27K-token state is refused (see D3 below).

## Smoke (RUN 2026-10-23, D1, two requests, $0.003)

`data/requests/smoke_d1.jsonl` = 513272 (17K chars, 24 gold mentions) + 809122's dissent (124K
chars, 114 gold mentions); run `smoke_d1`, both answered, 0 errors.

| | 513272 | 809122 dissent |
|---|---|---|
| state / schema / input tokens | 3,902 / 613 / 4,515 | 28,979 / 613 / 29,592 |
| passes, truncated, latency | 1, false, 0.5 s | 1, false, 1.6 s |
| list: unique values / spans | 51 / 61 | 255 / 504 |
| overlap precision / recall vs gold | 0.41 / 0.96 | 0.15 / 0.65 |
| list confidence | 2.6e-108 | 0 |

What the smoke settled:
- **No truncation at 8,192 tokens.** A 29K-token state went through in one pass with `truncated`
  false; the 8,192-token pass budget is about the fields, not the state. Billed input = state +
  schema, once. Whole dev set per design ≈ 344K + schema tokens ≈ $0.04. Chunking is not needed.
- **List answers: `value` holds the UNIQUE strings, `spans` every occurrence** (sorted,
  non-overlapping, offsets into `state`, 565/565 verified). `to_predictions.py` reads the spans
  and takes each mention's text from the state; the first version zipped values with spans and
  was wrong.
- **A list's `confidence` is ≈ 0 on both** (looks like a product over items) — not usable as a
  per-mention or per-list signal.
- **Quality out of the box is far below the floor.** Triage scorer on 513272: mention P 0.377 /
  R 0.958 / F1 0.541 (stored markup 0.884 on that opinion). Recall is high on the short opinion
  and drops to 0.65 on the long one (misses are mostly `Id., at …` / `Ibid.` / `301 U. S., at 640`
  short forms). Precision is the problem: the list is padded with single words (`The`, `I`, `In`,
  `the`, `“`), name fragments (`Quagli`, `Qu`), party names standing before their own citation
  (`Wickard v. Filburn, `), constitutional and statutory references (`Commerce Clause`, `U. S.
  Const.`, `26 U. S. C.`). Most of the junk is filterable by rule (length, stopwords, no digit
  and no capitalised name); whether the model's own precision improves with D2's narrower fields
  is the next question.

## Results — D1 and D2 on the 49 gold_dev clusters (RUN 2026-10-23, $0.084 together)

Both runs: 68 requests, 0 errors, 0 truncated, 1 pass each, 0.3 min wall at 2 workers. D1 billed
412,755 input tokens ($0.041), D2 426,763 ($0.043); schema tokens are the only difference.
Triage scorer, pooled over the 49 clusters, same gold as every row in
`../experiments_09022026/model_comparison_dev.md`:

| system | mention P | mention R | mention F1 | spans returned (gold 2,953) |
|---|---|---|---|---|
| stored CourtListener markup (floor) | 0.872 | 0.842 | 0.857 | 2,850 |
| published encoder r4 + Id. rule (reference) | 0.973 | 0.973 | 0.973 | – |
| **regex-only control, no model** (three patterns over the full text: reporter cite, Id./Ibid., supra) | 0.858 | 0.781 | **0.817** | 2,689 |
| Clairic D1 raw (one list field) | 0.290 | 0.675 | 0.406 | 6,868 |
| Clairic D1 + rule filter (`filter_predictions.py`) | 0.554 | 0.327 | 0.412 | 1,745 |
| Clairic D2 raw (three typed lists) | 0.133 | 0.975 | 0.234 | 21,646 |
| Clairic D2 + rule filter | 0.263 | 0.389 | 0.314 | 4,365 |
| regex control + Clairic D2 name-only spans passing the name rule | 0.456 | 0.906 | 0.607 | 5,866 |

How the spans sit on the gold (D2; D1 similar with lower recall): of 2,000 gold reporter
citations, 1,999 are overlapped by some span but 1,623 by two or more, mostly fragments
(`'805 F.2d '`, `'805 F.2d 1117'`, `'1117'`; `'701 F.2d '`, `'F'`, `'1078'`, `'1082'`). Gold
`Id.` forms: 248 of 311 overlapped, usually by a bare `'Id.'` without the page or by the page
alone. Supra forms: 161 of 162 touched, as 2–4 pieces. Name-only references: 475 of 480 found,
99% character coverage, but next to roughly 2,800 non-case names and words.

Reading:
- **Clairic recovers almost every mention but cannot delimit them.** D2 recall 0.975 comes from
  fragmentary and nested spans; under the scorer's one-to-one overlap matching every extra
  fragment is a false positive, so precision is 0.13–0.29.
- **A rule filter does not rescue it.** Dropping single words, fragments and names before their
  own citation lifts precision only to 0.26–0.55 while recall falls to 0.33–0.39, because the
  fragments that carried the recall fail the patterns. The filter rules were written after
  looking at this dev output, so these are the optimistic numbers.
- **Three regexes over the raw text score 0.817 with no model**, 4 points under the stored
  markup and double any Clairic variant. Clairic's one genuine contribution, the name-only
  references regex cannot see, comes with ten times as many false names: adding them to the
  regex spans moves F1 from 0.817 to 0.607.
- **No per-mention signal to threshold on.** A list carries one `confidence`, ≈ 0 on every
  request, so the junk cannot be ranked away.
- Cost and speed hold up: $0.04 and 20 seconds for the whole dev set.

Verdict for extraction (D1/D2): well below the floor and below a trivial regex; not a seeder,
not an adjudicator, not an extractor swap. D3 is below in its own section; D4 is moot without usable extraction.

## D3 — coreference by one field per cited case (RUN 2026-10-23, $0.137)

- **Request limit found.** The first D3 body (809122's lead opinion, 141 fields, 27K-token state)
  was refused: HTTP 422 `the request needs 97736 input tokens across 2 passes, over the limit of
  65529; shorten the input or send fewer fields`. The 65,536 limit is on state × passes + schema,
  and fields that overflow one pass multiply the state. `make_requests.py --max-fields 60` now
  splits the inventory across requests (86 requests, max 3 per opinion); every request then ran
  in one pass. Schema cost is real for this design: 60 fields = 19,122 schema tokens, so D3 billed
  1.32M tokens, 54% of it schema.
- 86 requests, 0 errors, 0 truncated, 0.8 min. 2,202 case fields answered, median 50 spans per
  field, p90 252, max 1,217; 73 fields empty. Field confidence is ≈ 0 on 2,162 of 2,202 fields
  and above 0.5 on 11.

| D3 variant | mention P | mention R | mention F1 | coref P | coref R | coref F1 | spans |
|---|---|---|---|---|---|---|---|
| stored markup (floor) | 0.872 | 0.842 | 0.857 | 0.996 | 0.955 | 0.975 | 2,850 |
| raw (every field's spans, first field wins a duplicate) | 0.041 | 0.963 | 0.078 | 0.072 | 0.355 | 0.120 | 69,767 |
| + rule filter | 0.368 | 0.646 | 0.469 | 0.383 | 0.349 | 0.365 | 5,181 |
| spans claimed by exactly one field + rule filter | 0.428 | 0.475 | 0.450 | 0.366 | 0.393 | 0.379 | 3,277 |

What the fields return: each case's list is the same spray of numbers, fragments and party names
as D1/D2, plus the other cases' citations. In 513272 the field for *Lee* returns `805 F.2d 1117`
(*Pugliese*) and the field for the cert-denied parallels returns both; of 69,767 distinct spans
38,136 are claimed by two or more fields, 12,915 by five or more. The per-case instruction does
not make the model discriminate between cases, so the grouping signal is weak: pairwise coref F1
tops out at 0.38 against the floor's 0.975.

Verdict for coreference: negative. D3 is the costliest design and the furthest from its floor.

### Overall verdict (2026-10-23)

Clairic as shipped does not extract or group case citations to a usable standard: best mention
F1 0.469 (D3 + rules) against a 0.857 floor and a 0.817 no-model regex control; best coref F1
0.38 against 0.975. It is fast (≤ 4 s for a 60-field, 29K-token request) and cheap ($0.22 for the
whole experiment), reads 29K-token states whole, and returns well-formed offsets, but its spans
are fragments, its lists carry no per-item signal and its list confidence is ≈ 0. The published
encoder stays the extractor; GPT-6 Sol seeding (round 3 route 2) stays the coref lever. Revisit
only if the open weights and a training recipe appear.

## Scoring

`to_predictions.py` writes the shape `score_encoder.py` reads (`{cid: {"opinions": [{"opinion_idx",
"opinion_type", "mentions": [{"text", "before", "group"}]}]}}`); the scorer re-anchors each mention
in the gold frame by text + preceding context, so the offset basis does not affect the score. D1 and
D2 mentions get singleton groups; D3 groups come from the field name (`case_{id}` → `inv{id}`).
Chunked opinions keep each span from the chunk that holds it at least half an overlap from a cut.

`selftest.py --design d1|d3` feeds the gold mentions back as fake answers and scores them: D1 and D3
return mention 1.000 (D3 coref 1.000) on three clusters and on the two longest (809122, 2369968),
chunked or whole. The `before` context is 120 characters (the encoder emits 40): with 40, two
identical citation strings with identical preceding context re-anchored to the same occurrence
and cost 3 of 722 mentions.

## Decision gates

- Adjudicator: keep only if Clairic adds correct mentions the encoder misses (disagreement study).
- Seeder: only if mention F1 beats the stored-markup floor 0.857.
- Coreference (D3): only if coref F1 reaches 0.975.
- gold_test is not scored in this experiment.

Risks carried into any write-up: preview status with no SLA and closed weights, undocumented rate
limits and no batch path, every accuracy claim vendor-reported.

## Layout

| file | role |
|---|---|
| `run.sh` | uv ephemeral runner (tiktoken, httpx) |
| `build_inputs.py` | step 1: gold-frame opinions + inventory + sizing |
| `prompts/fields_d1.json`, `fields_d2.json`, `fields_d3.json` | field designs with the span convention in the instructions |
| `make_requests.py` | step 2: request bodies per design, optional chunking |
| `run_clairic.py` | step 3: Workers AI REST transport (resume, retries, dry-run, limit) + status |
| `to_predictions.py` | step 4: answers → `data/pred_{run}.json` + usage report |
| `filter_predictions.py` | rule post-filter over a prediction file (reporter / Id. / supra / name rules, overlap collapse) |
| `selftest.py` | offline wiring check from gold |
| `data/` (gitignored) | `inputs/`, `requests/`, `runs/{name}/outputs.jsonl`, `pred_*.json` (incl. `pred_regex_control.json`, `pred_hybrid_regex_d2names.json`) |
