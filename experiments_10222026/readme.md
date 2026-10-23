# 1022 — Decision ("System One") models as the citator gate

Tests whether a fast, non-reasoning decision model can replace the GPT-5.6 reasoning gate
(experiments_09112026, `gate_v6`). A decision model takes a `state` (text or JSON) plus typed
questions (choice / yes-no / score) and returns calibrated probabilities in one forward pass; it
never generates text. The gate question per cited case is either a yes/no (does the Citing Case
treat this case as more than merely cited?) or one choice over the nine treatments.

Dataset: the 41 Dahl pairs of the external benchmark (`flp/external-benchmark/output/
external_benchmark.csv`, `source=dahl`), 17 SCOTUS citing opinions. Their gate inputs (tagged
opinion + cited-case inventory) already exist in `../experiments_09112026/data/inputs/`.

## Conclusion (2026-10-22)

Set: Dahl's 41 negative pairs (citing opinion → overruled case) fall in 17 distinct citing
opinions (Harper alone has 12). Those 17 opinions are also FLP benchmark clusters where the
experts labeled every cited case, which gives 1,124 expert-labeled pairs in the same opinions:
105 negative by any expert's vote, 1,080 Cited by. Dahl measures recall on overrulings; the FLP
pairs add the neutral cases that make false-flag rates measurable.

- **Decision models fit the gate, not the labeler.** They return probabilities only, so they
  cannot produce the quote and rationale the labeler must return; the labeler stays a text model.
- **mammoth-1 with the nine-option treatment question matches the two-pass GPT-5.6 gate.**
  Recall of expert negatives 60 vs 62 of 105, with fewer false flags on Cited-by cases (9.4% vs
  10.9%), from one request per cited case over its concatenated mention windows. A lower
  threshold lifts recall to 72 of 105 at 14.9% false flags.
- **Its recognized-treatment mark calibrates like the GPT gate's**: 130–147 cases marked against
  139.
- **Cost**: free on Baseten's tier this week against $0.73 for the two GPT passes on the same
  opinions; minutes instead of batch queues.
- **The treatment-choice question is the gate question to keep.** The yes/no "more than merely
  cited" phrasing needs roughly twice the false flags for equal recall on both models tested.
- **Jev 1.14 behaves the same way, one step behind mammoth-1**: 59 of 105 at 10.6% false flags
  in choice mode.
- **Jev as a one-shot labeler finds more but flags more.** 58 vs 47 of 105 negatives and 29 vs 24
  of 44 exact labels against today's pipeline, at twice the false flags, and without quote or
  rationale.

## Candidate models

Performance columns are the two public decision-model indexes (Jev Decision Index 0.4,
community-run; Artificial Analysis Decision Index, 2026-10-19 snapshot). All figures are vendor-
or community-reported, and neither index tests long legal documents.

| Model | Base model & architecture | Context | Price (input only, output free) | Jev DI 0.4 / AA DI | Weights & access |
|---|---|---|---|---|---|
| Jev 1.14 (TypeSafe) | Undisclosed, non-autoregressive, synthetic training data only | 64K per request, 32K per question; 262K with opt-in header (slower, less accurate) | $0.042/M | 62.2 / 57.1 | Closed, hosted only, early-access waitlist |
| mammoth-1 (Base Labs) | Qwen3.5-27B full fine-tune; block-bidirectional attention on full-attention layers, GDN layers causal; softmax over option letters in one pass | 16,384 hard cap, longer requests refused | Free on Baseten until 2026-10-27, unpriced after; self-host ~54 GB BF16 | 71.2 (#1 of 208) / 65.9 (own replica) | Open, MIT |
| elephant-1 (Base Labs) | Qwen3.5-9B, same recipe as mammoth-1 | 16,384 hard cap | Same as mammoth-1; self-host ~18 GB BF16 | 62.7 (best under 15B) / 65.2 (own replica) | Open, MIT |
| pplx-decider-v1.2 (Perplexity) | Qwen3.8-27B full fine-tune; non-causal full attention with per-marker readout; all questions in one pass | 262,144 position limit, trained to ~32K | $0.02/M on Vercel and OpenRouter; 4B Flash at $0.01/M; self-host ~52 GB | 69.4 / not listed | Open, Apache 2.0 |
| d1-ultra (Liquid AI) | Undisclosed, "based on a larger open-weight model"; trained to abstain often | 32,768 | $0.042/M | not listed / 65.2 (#1 on AA) | Closed, hosted on Liquid, OpenRouter, Vercel |
| *Reference: GPT-5.6 Luna gate (0911)* | Reasoning model at effort high, two passes unioned | Whole opinion | ≈ $0.024 per opinion | Recall 0.82 at 10% flagged on the benchmark | Hosted (Bedrock, OpenAI Batch) |

## Step 1 — opinion length vs context window (DONE 2026-10-22)

`bash run.sh opinion_length.py` → `data/opinion_length.csv`. Tokens counted on the tagged
opinion (the gate's `<citedCase group="N">` markup included) with the Qwen3 tokenizer (the
backbone family of mammoth-1, elephant-1 and pplx-decider) and with o200k as a proxy for the
undisclosed models. The tags add 10–20% over plain text. "Fits" = tagged opinion + one 40-token
question within the per-question limit AND opinion + one question per cited case within the
per-request limit.

### Opinions that fit whole, of 17

| model | fit | fail |
|---|---|---|
| jev-1.14 default (64K / 32K per question) | 12 | 5 |
| jev-1.14 long-context header (262K) | 17 | 0 |
| mammoth-1 (16K) | 7 | 10 |
| elephant-1 (16K) | 7 | 10 |
| pplx-decider-v1.2 (262K, trained to 32K) | 17 | 0 |
| d1-ultra (32K) | 11 | 6 |

### Per opinion (Qwen tokens, tagged)

| cluster | case | cited cases | o200k plain | qwen tagged | jev default | mammoth / elephant | d1-ultra |
|---|---|---|---|---|---|---|---|
| 93904 | Roberts v. Lewis | 11 | 2,045 | 2,308 | yes | yes | yes |
| 110212 | Trammel v. United States | 21 | 6,618 | 8,162 | yes | yes | yes |
| 103786 | Penn Dairies v. Milk Control Comm'n | 35 | 7,914 | 9,002 | yes | yes | yes |
| 103549 | Baltimore & Ohio R. Co. v. Kepner | 68 | 7,535 | 9,605 | yes | yes | yes |
| 117989 | Yamaha Motor Corp. v. Calhoun | 40 | 7,436 | 9,893 | yes | yes | yes |
| 103172 | Graves v. New York ex rel. O'Keefe | 60 | 8,003 | 10,018 | yes | yes | yes |
| 106628 | Wheeldin v. Wheeler | 88 | 9,609 | 12,202 | yes | yes | yes |
| 117905 | Arizona v. Evans | 51 | 14,121 | 17,954 | yes | no | yes |
| 118317 | College Savings Bank v. Florida Prepaid | 52 | 16,494 | 20,434 | yes | no | yes |
| 110468 | City of Milwaukee v. Illinois | 72 | 22,607 | 27,018 | yes | no | yes |
| 110461 | City of Memphis v. Greene | 41 | 25,610 | 28,259 | yes | no | yes |
| 2528498 | Gasperini v. Center for Humanities | 81 | 25,607 | 31,272 | yes | no | no |
| 112890 | Harper v. Virginia Dept. of Taxation | 100 | 23,141 | 32,027 | no | no | no |
| 111308 | Garcia v. San Antonio MTA | 87 | 27,329 | 33,487 | no | no | no |
| 106447 | Glidden Co. v. Zdanok | 181 | 33,074 | 40,529 | no | no | no |
| 106548 | Fay v. Noia | 231 | 37,909 | 47,221 | no | no | no |
| 111094 | Pennhurst State School v. Halderman | 158 | 39,525 | 51,667 | no | no | no |

Median 20,434 tokens, max 51,667, total 391K tokens for the 17 (pplx-decider and jev long-context
fit all 17 and are omitted from the per-opinion columns). The Dahl set skews long: these are
SCOTUS opinions with 11–231 cited cases each, so whole-opinion input is not an option for the
16K models on 10 of 17, and the per-mention window set-up is required for every model to cover
the full set. Whole-opinion input also runs into the documented accuracy drop from unrelated
content ("context rot") that every vendor warns about, so every set-up below uses mention
windows; whole-opinion input was not built.

## Step 2 — the two input set-ups (BUILT 2026-10-22, no model calls)

One cited case per question, always named explicitly. `bash run.sh build_contexts.py` (flags
`--before 256 --after 512 --budget 16000`) → `data/contexts/`. Every cited case of the 17
opinions is built (1,377 cases, 4,019 mentions), not only the 41 Dahl pairs, so flag rate on the
other cases can be measured against the FLP expert labels for these benchmark clusters.

- **A — per mention** (`mention.jsonl`): one record per mention = 256 Qwen tokens before + the
  mention + 512 after (asymmetric, because a treatment usually follows the citation). Pair
  flagged when any mention record is flagged.
- **B — concatenated** (`concat.jsonl`): all mention windows of the case (same 256/512),
  overlapping windows merged, concatenated in document order with `[…]` separators and packed
  into chunks of at most 16,000 tokens (the floor across candidates; mammoth/elephant cap at
  16,384). A case with several chunks gets several records; pair flagged when any chunk is.
- Both: the target's mentions are wrapped in `<cite>…</cite>`, other cases are plain text, all
  gate markup removed; `parts` records which sub-opinions (lead / concurrence / dissent) the
  passage spans. Question + criteria in `prompts/gate_v1.json` (yes/no, gate_v6 definitions:
  true = this court treats the target as more than merely cited; false = merely cited, treatment
  reported from another court, or case on appeal). State fields: `citing_case`, `cited_case`
  (name + citations), `passage`.

| setup | records | cited cases | passage tokens | cost at $0.042/M | at $0.02/M |
|---|---|---|---|---|---|
| A mention | 4,019 | 1,377 | 3.10M | $0.13 | $0.06 |
| B concat | 1,380 | 1,377 | 2.01M | $0.09 | $0.04 |

Record sizes: A 264–798 tokens (median 779); B median 785, 20 records over 8K, max 14,944; only
3 cited cases need two chunks (National League of Cities in Garcia, Davis and Chevron Oil in
Harper). Dahl pairs: 41/41 joined to inventory ids (40 by cluster id, Griffin v. California in
Harper by name) → `data/contexts/pairs.csv` with mention counts (1–102 per pair) and record counts.
All 41 Dahl pairs are negatives, so Dahl measures recall only; precision comes from the non-Dahl
cases of the same opinions (FLP expert labels, 0911 gate flags).

## Step 3 — Jev as a one-shot labeler, choice mode (RUN 2026-10-22)

Experiment 2 runs first: no gate, no direct history. Jev is asked, per cited case, for the
treatment directly. `bash run.sh make_requests.py --experiment label --model jev-1.14.0` →
`data/requests/jev-1.14.0_label_{mention,concat}.jsonl` (4,019 + 1,380 bodies). Each body is
`state = {citing_case, cited_case, passage}` plus 22 questions from `prompts/label_v2.json`,
evaluated independently against the same state (one input charge per request; the questions
add ≈ 2,900 tokens per request):

- `applied` — one choice over 9 options: the 8 Citing Reference labels + Cited by, with the
  labeler_v3 definitions. Reported treatments and the case on appeal are "Cited by" here.
- `rec:<label>` — one yes/no (noul) per recognized label, 21 in all (13 direct-history + 8
  analytical, neutral history such as "aff'd" and "cert. denied" included): does the passage
  report that another court applied this treatment to the target? A noul per label returns
  every reported treatment, where a single choice would return only the most severe.

Per cited case the records are pooled: applied = the non-neutral choice with the highest
confidence across records (`--pool confidence`, default; `--pool severity` takes the most severe;
one record in B except 3 cases); recognized = every label whose noul ≥ 0.5 in any record (union);
P(flag) = max of 1 − P(Cited by) and the negative-tier recognized nouls, which gives a threshold
curve the GPT gate cannot.

The same bodies run on any candidate by changing `--model`; the runner `run_systemone.py`
(httpx, async, `--workers`, retries on 429/5xx/529, resume by key, `--limit` smoke, `--dry-run`
prints the first body and sends nothing, refuses to run without `$TYPESAFE_API_KEY`,
`--base-url`/`--api-key-env` for other gateways) stores every answer with its probabilities.

```bash
# needs a go: Jev early-access key in TYPESAFE_API_KEY
bash run.sh run_systemone.py run --name jev_label_concat  --requests data/requests/jev-1.14.0_label_concat.jsonl --dry-run
bash run.sh run_systemone.py run --name jev_label_concat  --requests data/requests/jev-1.14.0_label_concat.jsonl --limit 20   # smoke
bash run.sh run_systemone.py run --name jev_label_concat  --requests data/requests/jev-1.14.0_label_concat.jsonl              # ≈ $0.25
bash run.sh run_systemone.py run --name jev_label_mention --requests data/requests/jev-1.14.0_label_mention.jsonl             # ≈ $0.62
bash run.sh run_systemone.py status --name jev_label_concat
bash run.sh score.py --run jev_label_concat --run jev_label_mention [--flag-threshold 0.5] [--rec-threshold 0.5]
```

Scoring (`score.py`, writes `data/runs/{name}/pairs.csv`):
- binary negative = applied ≠ Cited by OR recognized negative (tier Stop/Warning/Caution):
  Dahl recall (41), any-expert recall (105) and majority recall (44) on the 1,129 benchmark gold
  pairs of these opinions, false-flag rate on Cited-by, precision; plus the probability
  threshold sweep;
- applied label exact and tier-exact on the 44 gold positives, precision of predicted labels,
  and the gold → predicted confusion list;
- recognized: detected / gold label present in the predicted set, on the 11 pairs with a
  recognized final in these opinions (few: the benchmark rarely records neutral history), the
  number of cases and labels predicted, and the per-label counts.

Reference on the same pairs: the 0911 gate (two-pass union ≥ medium) recovers 62/105
any-expert positives at 9.3% false-flag and 16/41 Dahl pairs; union OR recognized-marked
75/105, 14.0%, 31/41 (Dahl is mostly overruling that the opinion reports or applies).

### Cost on the same 17 opinions: Jev one-shot vs the 0911 pipeline

Measured from the 0911 runs' raw usage for these 17 clusters (GPT-5.6 Luna gate ×2 at effort
high; Sonnet 4.6 labeler_v3 on the gate's routed ids); Jev from the full runs' usage.

| system | what it does | tokens | cost, 17 opinions | per opinion |
|---|---|---|---|---|
| Jev one-shot, concat (B) | applied + recognized labels for all 1,377 cited cases | 6.89M in (2.0M passage, the rest the 22 questions), out free | $0.29 | $0.017 |
| Jev one-shot, per mention (A) | same, 4,019 records | 17.3M in | $0.73 | $0.043 |
| GPT gate, 2 passes | flags + recognized marks + case on appeal + disposition | 0.99M in, 0.39M out (reasoning) | $0.73 on demand / $0.37 OpenAI batch | $0.043 / $0.022 |
| GPT gate ×2 → Sonnet labeler_v3 | the full pipeline: labels for the routed ids only | + 0.46M in, 0.13M out | $2.43 on demand / $1.70 + $0.37 = $2.07 batch | $0.14 / $0.12 |

Jev B costs 40% of the on-demand gate alone (78% of the batch gate) and ≈ 14% of gate + labeler,
while labeling every cited case rather than the routed ones. Most of Jev's tokens are the 22
questions repeated per request (≈ 2,900 of ≈ 5,000 tokens): batching several cited cases of one opinion into one request with
the passage per case in the state would cut that, at the price of the one-citation-per-question
design. Jev does not produce the disposition or the case on appeal; those stay with the gate or
a separate step.

## Results — Jev 1.14 one-shot labeler vs today's pipeline (RUN 2026-10-22)

Both Jev sets ran in full with 0 errors: concat 1,380 requests, 6.89M input tokens, $0.29,
0.4 min; per-mention 4,019 requests, 17.3M tokens, $0.73, 1.4 min (6 workers). Scored with
`score.py --run jev_label_mention --run jev_label_concat` (applied label pooled by highest
confidence, recognized labels at ≥ 0.5). The pipeline column is the 0911 gate_v6 ×2 → Sonnet
labeler_v3 outputs on the same 17 opinions. "Negative" everywhere = applied label other than
Cited by OR a negative-tier recognized label; benchmark truth = any-expert vote on the 1,124
gold pairs (105 negatives); Dahl = 41 negative pairs.

| | Jev per-mention | Jev concat | Two-stage pipeline (GPT gate ×2 → Sonnet) |
|---|---|---|---|
| Dahl pairs called negative (of 41) | **34** | 30 | 30 |
| Benchmark negatives caught (of 105) | **58** | 45 | 47 |
| Cited-by left alone (of 1,080) | 976 | 1,019 | **1,036** |
| All benchmark pairs correct (of 1,124) | 994 | 1,019 | **1,038** |
| Exact label on gold positives (of 44) | **29** | 23 | 24 |
| Cost, 17 opinions | $0.73 | **$0.29** | $2.07 batch |
| Wall time | 1.4 min | 0.4 min | hours (batch queues) |
| Disposition and case on appeal | no | no | yes |
| Recognized treatments | 21 labels, unvalidated (11 gold pairs) | same | labeled with acting case + quote |

Reading: Jev per-mention finds more (11 more benchmark negatives, 4 more Dahl pairs, 5 more exact
labels) at a third of the cost and in minutes; the pipeline is cleaner (44 false flags on Cited-by
against 104) and still does the direct-history jobs. The two miss different cases (Jev per-mention
and the GPT gate together would reach 71/105). Jev's main labeling error is a treatment aimed at a
neighbouring case inside a mention window credited to the tagged target (e.g. Little v. Barreme,
Wise v. Withers, Mitchell v. Harmony in Wheeldin: one mention says Overruled at 0.90+, the others
and the concat passage say Cited by) — a prompt/passage fix, not a pooling one. Pooling study on
the same outputs (exact labels of 44): most-severe 27, probability mass 27, majority vote 27,
highest-confidence non-neutral 29 (default), two-stage A-flags→B-labels 30. Per-pair CSVs with
every score, label and probability: `data/runs/jev_label_{mention,concat}/pairs.csv`.

## Experiment 1 — Jev as the gate of today's pipeline (RUN 2026-10-22)

`make_requests.py --experiment gate` sends two yes/no questions per record from
`prompts/gate_v1.json`, mirroring gate_v6's two marks: `more` (does the Citing Case itself do more
than merely cite the target) and `recognized` (does the passage report another court's treatment
of it, neutral history included). No direct history (the GPT gate's disposition / case-on-appeal
job is not replaced here). Runs: concat 1,380
requests, 3.48M tokens, $0.15; per-mention 4,019 requests, 7.38M tokens, $0.31; 0 errors.
Per cited case P(more) and P(recognized) are max-pooled over records.

### Gate quality vs the GPT gate on the same 17 opinions (`score_gate.py`)

| gate | Dahl (of 41) | any-expert (of 105) | majority (of 44) | false flags on Cited-by (of 1,080) |
|---|---|---|---|---|
| GPT gate ×2, union ≥ medium | 16 | 62 | 39 | 118 (10.9%) |
| GPT gate ×2, union OR recognized | 31 | 75 | 39 | 179 (16.5%) |
| Jev per-mention, more ≥ 0.7 | 21 | 62 | 39 | 201 (18.5%) |
| Jev per-mention, more ≥ 0.6 | 24 | 68 | 40 | 244 (22.5%) |
| Jev per-mention, more OR recognized ≥ 0.6 | 35 | 76 | 40 | 296 (27.3%) |
| Jev concat, more ≥ 0.8 | 17 | 44 | 33 | 125 (11.5%) |
| Jev concat, more ≥ 0.6 | 25 | 67 | 39 | 224 (20.6%) |
| *Jev one-shot labeler, per-mention (applied ≠ Cited by OR negative recognized), for reference* | 34 | 58 | 37 | 104 (9.6%) |

At the GPT gate's recall the Jev `more` noul flags about twice as many Cited-by cases (18.5% vs
10.9%). Its own choice question from experiment 2 is a tighter gate than its yes/no question:
58/105 at 9.6% false flags, and by probability (1 − P(Cited by) ≥ 0.4) 68/105 at 11.9%. The
`recognized` noul marks 210–223 cases against the GPT gate's 139 (overlap ≈ 90).

The assembled 0911-format runs (`assemble_gate.py`, thresholds high 0.8 / medium 0.6 / low 0.4,
recognized 0.5) score on the standard 0911 scorer (`score.py --gate jev_gate_mention --split
all`) as: ≥ medium any-expert 69/105 at 218 false pairs (25% of pairs flagged), ≥ high 54/105 at
143 (17%); concat ≥ medium 68/105 at 197 (24%).

## Experiment 1b — mammoth-1 as the gate, yes/no vs choice mode (RUN 2026-10-22)

Decision models cannot write the quote and rationale the labeler produces, so their role is the
gate; the labeler stays with Sonnet. mammoth-1 (Base Labs, 27B, Baseten free week, `Authorization:
Api-Key`, model `decision-models/baseten-mammoth-27b-latest`) ran on the same request files in two
question modes: **yes/no** (`gate_v1`: `more` + `recognized` nouls) and **choice** (`gate_choice_v1`:
the 9-option applied-treatment question alone; gate score = 1 − P(Cited by)). Four sets, 10,798
requests, 0 unanswered after re-sending 167 rate-limited ones (429s at 6 workers; ≈ 2 req/s, 32 min
for per-mention, 11 min for concat). Full sweep in `gate_sweep.md` (`score_gate.py`).

### Gates at the GPT gate's operating point (same 17 opinions, 1,080 Cited-by pairs)

| gate | threshold | any-expert (of 105) | majority (of 44) | Dahl (of 41) | false flags on Cited-by |
|---|---|---|---|---|---|
| GPT-5.6 gate ×2, union ≥ medium | — | 62 | 39 | 16 | 118 (10.9%) |
| **mammoth choice, concat** | 0.3 | 60 | 38 | 24 | **101 (9.4%)** |
| mammoth choice, per-mention | 0.4 | 60 | 38 | 21 | 117 (10.8%) |
| mammoth choice, per-mention | 0.3 | **72** | **41** | 27 | 161 (14.9%) |
| mammoth yes/no, per-mention | 0.3 | 61 | 40 | 23 | 217 (20.1%) |
| mammoth yes/no, concat | 0.3 | 54 | 39 | 21 | 165 (15.3%) |
| Jev choice (one-shot run), per-mention | 0.4 | 59 | 39 | 20 | 114 (10.6%) |
| Jev yes/no, per-mention | 0.7 | 62 | 39 | 21 | 201 (18.6%) |

With the recognized mark added (the GPT gate routes recognized ids to the labeler too):

| gate | any-expert (of 105) | Dahl (of 41) | false flags on Cited-by |
|---|---|---|---|
| GPT gate ×2, union ≥ medium OR recognized | 75 | 31 | 179 (16.5%) |
| mammoth choice concat ≥ 0.3 OR yes/no recognized ≥ 0.5 | 68 | 31 | 155 (14.4%) |
| mammoth choice per-mention ≥ 0.4 OR recognized ≥ 0.5 | 70 | 32 | 178 (16.5%) |
| mammoth choice per-mention ≥ 0.3 OR recognized ≥ 0.5 | 81 | 35 | 215 (19.9%) |

Findings:
- **Choice mode beats yes/no mode on both models.** The applied-treatment question with 9 named
  options is a better-calibrated gate than "does the court do more than cite"; the yes/no gate
  needs roughly twice the false flags for the same recall.
- **mammoth-1 in choice mode is a drop-in for the GPT gate** on these opinions: concat at 0.3 gives
  GPT's recall with fewer false flags from 1,380 requests; per-mention at 0.3 trades 44 more false
  flags for 10 more positives. Jev's choice gate sits a little below mammoth's.
- **mammoth's recognized mark calibrates like the GPT gate's** (130–147 cases marked at ≥ 0.5 vs
  139; Jev marked 210–223).
- Cost: free on Baseten this week; self-hosted it is a 27B model on one GPU. The GPT gate cost
  $0.73 on demand / $0.37 batch for the same opinions.

Next for the gate track: a combined prompt (`applied` choice + `recognized` noul in one request),
then the Sonnet labeler on mammoth's routed ids to compare the downstream labels with the
GPT-gated pipeline (`assemble_gate.py` needs a choice-mode input to write the 0911-format run).


## Layout

| file | role |
|---|---|
| `run.sh` | uv ephemeral runner (tiktoken, transformers tokenizer, httpx) |
| `opinion_length.py` | step 1: token counts + fit per model |
| `build_contexts.py` | step 2: setup A (`mention.jsonl`) + setup B (`concat.jsonl`) + `pairs.csv` + `params.json` |
| `prompts/label_v2.json` | experiment 2: `applied` choice + 21 `rec:<label>` nouls, with definitions and the key → label map |
| `prompts/gate_v1.json`, `prompts/gate_choice_v1.json` | experiment 1: gate nouls (yes/no mode), applied choice alone (choice mode) |
| `make_requests.py` | step 3: records → `/v1/systemone` request bodies per experiment, model and setup |
| `run_systemone.py` | generic System One client: run (resume, retries, dry-run, limit, `--auth-scheme Api-Key` for Baseten) + status |
| `score.py` | experiment 2 scorer: pooled labels (`--pool confidence|severity`), binary + label + recognized metrics, threshold sweep |
| `score_gate.py` | experiment 1: gate threshold sweep next to the GPT gate (yes/no and choice runs) |
| `gate_sweep.md` | experiment 1b: full gate threshold sweep (Jev + mammoth-1, both modes and set-ups) from `score_gate.py` |
| `assemble_gate.py` | experiment 1: pooled gate answers → 0911-format gate run (`experiments_09112026/data/runs/jev_gate_*`) for the 0911 scorer and `run_stage.py prepare --from-gate` |
| `data/` (gitignored) | `opinion_length.csv`, `contexts/`, `requests/`, `runs/` |
