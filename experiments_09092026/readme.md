# 0909 — Citation extraction + coreference encoder on CaseLawModernBERT-large

`experiments_09022026` (the citator quality review) turned up eyecite's
extraction/coreference error rate as a real problem, motivating a trainable
encoder that does both tasks (Task A: BIO extraction over case-citation
spans; Task B: antecedent-ranking coreference) directly on opinion text —
something that can eventually run instead of, or alongside, eyecite. Measured
throughout against the 49-cluster human-verified dev set from that experiment.

## What was tried

1. **Round 1 (silver):** ~20,000 CLReplica opinions labeled straight from
   eyecite's own output — no human or LLM correction. Question: how far does
   eyecite supervision alone get a continue-pretrained ModernBERT-large, and
   does the model just relearn eyecite's mistakes along with its labels?
2. **Round 2 (GPT-seeded):** 10,000 new opinions, oversampled for negative
   treatment language, with citation grouping corrected by GPT-5.6 Luna (model id `gpt-5.6-luna`;
   the seeding pipeline built in `experiments_09022026`) before training — plus a
   warm-start fine-tune from the round-1 checkpoints against a cold-start
   control, to see whether silver pretraining is worth carrying forward.

Harness ported from `experiments_06142026/finetune` (Exp 3): tag-based splits
instead of CV folds, `CaseLawModernBERT-large` backbone, step-based eval for
the larger corpus. Reproduce-it commands are in **How to reproduce** below;
this section is the findings.

## Findings & conclusions

### Round 1: the model inherits eyecite's recall ceiling, but not its precision failures

Trained on 13,304 opinions (eyecite labels only), scored against the same 49
gold_dev clusters used throughout `experiments_09022026`:

| Task A — extraction | span P | span R | span F1 | recall on curator-added (eyecite missed) | curator-removed spans still tagged |
|---|---|---|---|---|---|
| eyecite (floor) | 0.858 | 0.896 | 0.877 | 0.000 | 1.000 |
| **silver model** | **0.911** | 0.833 | 0.870 | 0.090 | **0.032** |

The model reproduces eyecite's recall ceiling almost exactly (it recovers 879
of eyecite's 956 correct spans and only 10 of the 111 spans a curator added
that eyecite missed) — expected, since eyecite labels are all it ever saw. But
it does **not** reproduce eyecite's precision failures: it learned on its own
to leave statutes, regulations and record cites untagged, dropping the
false-positive rate on curator-removed spans from 100% to 3%. Net F1 ties the
floor (0.870 vs 0.877): recall down, precision up. That's the useful result —
supervised structure (BIO tagging) generalizes past eyecite's specific
mistakes even without a single corrected label.

Task B (coreference) tells a similar story: B³ F1 0.947 on gold_dev
(attach rate 0.888), and it treats curator-added mentions (0.889 attach) as
well as eyecite-found ones (0.887) — the linker generalizes beyond the silver
grouping it trained on, it isn't just memorizing eyecite's groups.

**Conclusion:** the 8% of eyecite-found spans the model drops and the 91% of
curator-added spans it misses are exactly what round 2's corrected labels
should fix. Silver alone is a plausible floor, not a ceiling.

### Where the encoder stands against LLM-based correction (apples-to-apples, 49 dev clusters)

Same triage scorer as every row in `experiments_09022026`'s model comparison:

| system | mention F1 | coref F1 |
|---|---|---|
| eyecite (no correction) | 0.857 | 0.975 |
| **silver encoder** (this round, no gold mentions at inference) | **0.914** | **0.933** |
| Kimi K2.5 (LLM edit of eyecite) | 0.858 | 0.984 |
| GPT-5.6 Luna, v5 prompt | 0.920 | 0.990 |
| Sonnet 4.6 | 0.932 | 0.992 |
| GPT-5.6 Luna, v8g + candidates | 0.972 | 0.991 |
| Opus 5 | 0.993 | 0.993 |

The encoder is the only row that re-extracts from raw text rather than
editing eyecite's output, so its precision gain over eyecite is genuine, not
borrowed. It already beats eyecite and Kimi cold. Its coref F1 sits below
every LLM row because the LLMs start from eyecite's grouping (already 0.975)
and only edit it, while the linker here runs on its own extraction. Full
cross-model table with prices/specs: `../experiments_09022026/model_comparison_dev.md`.

### Round 2: 10,000-opinion GPT-seeded sample, built and packaged

Sampled 10,000 CLReplica opinions oversampled for negative-treatment language
(2026-09-11; scotus 919 / fed appellate 2,385 / fed district 2,426 / state
supreme 2,225 / state appellate 2,045; mean 33.5K chars). Ran GPT-5.6 Luna
(prompt v8g + candidates, high reasoning effort) over all of them through the
OpenAI Batch API (178M input
+ 104M output tokens, ~7.3h wall time across 4 batch parts), applying 191,339
corrections (adds/removes/merges/regroups) to eyecite's grouping. Six
clusters failed — five hit the 128K output ceiling on very long opinions, one
was refused by OpenAI's content filter — and were **dropped rather than
retried** (2026-09-14). Measured 2026-09-21 against the inputs sent
(`data/citation_seed_r2/inputs/{cid}.txt`, chars after tag stripping; eyecite
mentions = `<c>` tags; cited cases = inventory entries): 8895033 *United
States v. Spock* 107,014 chars / 474 / 197 (cap); 8891100 79,613 / 169 / 47
(cap); 2723464 52,972 / 100 / 22 (cap); 6557048 52,949 / 39 / 32 (cap);
5958284 20,500 / 43 / 26 (cap); 112202 *DeShaney* 48,358 / 128 / 60 (content
filter). Corpus reference (400-input sample): median 21,924 chars / 39
mentions, p90 70,828 / 129. Only *Spock* is unusually long — four of the five
cap failures are median-to-p90 inputs, so the cap was hit by an over-long
response, not by input size. Untried remedies: edits-only response format,
pagination + merge (as in 0911 above 600K chars), or routing failures to the
encoder.

Built into a labeled dataset (`citations_r2.jsonl`, 420 MB):

> **Split names changed 2026-09-22** ahead of publication: `train_gpt` → `train`,
> `val_gpt` → `validation`, `train_llm` → `train_high_quality`; `gold_dev` and
> `gold_test` unchanged. The data file, `stats_r2.json` and the whole harness were
> updated together; `citations_r2.jsonl.pre_rename_backup` holds the old file and is
> deletable. Artifacts produced before that date keep old names in their FILENAMES
> (`pred_train_gpt_val_gpt_r4_rule.json`, `isolated_val_gpt_r5*.json`); their contents
> are unaffected, since neither stores a split tag. Scripts now derive new names, so a
> re-run of `run_predict_pod.sh` writes `pred_train_validation_r4_rule.json`.
>
> **Mention field renamed 2026-09-22**: a mention's coreference group is now
> `group`, not `cluster`, so it cannot be confused with `citing_cluster_id`
> (the CourtListener cluster). `mention_features*.jsonl` renames `gold_cluster`
> → `gold_group`, and `stats_r2.json` renames `clusters_*` → `groups_*`.
> Writers emit `group`; readers (`common.mention_list`, `score_encoder`) accept
> either key, so round-1 `citations.jsonl` and prediction files written before
> the rename still load. `*.pre_group_rename` copies are the old files and are
> deletable.

| split | clusters | mentions | GPT-added | GPT-removed |
|---|---|---|---|---|
| train | 9,770 | 680,723 | 98,939 (14.5%) | 56,696 |
| validation | 214 | 14,811 | 1,873 | 1,238 |
| train_high_quality (Opus-corrected, from 0902) | 261 | 42,577 | — | — |
| gold_dev / gold_test | 49 / 50 | 2,960 / 3,885 | – | – |

2% of GPT-seeded clusters (by hash) are held out as `validation` so checkpoint
selection uses the same label distribution as training, rather than eyecite
silver. Packaged into a 2.9 GB pod bundle that includes the round-1 silver
checkpoints, for a warm-start fine-tune (`--init` from those checkpoints, 2
epochs) against a cold-start control (fresh backbone, 3 epochs) — both train
on `train,train_high_quality`, select on `validation`, report on gold_dev.

### Round 2 results: the encoder closes most of the gap to GPT-5.6 Luna (2026-09-15)

Both warm and cold finished and scored end to end (extraction → linking, no
gold mentions given, same triage scorer as everywhere else):

| system | mention F1 | coref F1 |
|---|---|---|
| eyecite (no correction) | 0.857 | 0.975 |
| round-1 silver encoder | 0.914 | 0.933 |
| Kimi K2.5 | 0.858 | 0.984 |
| GPT-5.6 Luna, v5 | 0.920 | 0.990 |
| Sonnet 4.6 | 0.932 | 0.992 |
| **round-2 cold encoder** | **0.969** | **0.963** |
| GPT-5.6 Luna, v8g + candidates | 0.972 | 0.991 |
| **round-2 warm encoder** | **0.973** | **0.961** |
| Opus 5 | 0.993 | 0.993 |

Round-2 training essentially **closes the gap to GPT-5.6 Luna's mention F1**
(0.973/0.969 vs 0.972 — up from round 1's 0.914) at inference-time cost of a
single forward pass instead of an LLM call. Coref F1 (0.96–0.96) is the
remaining weak spot: it trails every LLM row (0.98–0.99) and even plain
eyecite (0.975) — the linker's own isolated B³-over-gold-mentions score is
strong (0.969, see below), so end to end it's inheriting extraction's
mistakes into the pairs it clusters, not failing on its own terms.

Warm vs. cold, isolated per task (extraction = exact char spans; linking =
B³ given *gold* mentions, not predicted):

| | extraction F1 | recall on GPT-added mentions | linking B³ F1 |
|---|---|---|---|
| round-1 silver | 0.870 | 0.090 | 0.947 |
| round-2 cold | 0.879 | 0.678 | 0.969 |
| round-2 warm | **0.893** | **0.724** | 0.969 |

Warm-starting from the silver checkpoints gives a clear, consistent edge on
**extraction** (better precision, recall, and especially recall on the
mentions eyecite never tagged). It makes **no real difference on linking** —
warm and cold tie at B³ F1 0.969 (0.0008 apart, noise). The GPT-seeded data
is what moves linking; the silver head start only helps the extraction side.

**Fixed along the way:** linking batches every mention in a case through the
encoder in one forward pass with no cap on case size; round 2's oversampling
for long opinions produced two extreme outliers (6,122 and 1,341 mentions,
vs. a median of 44) that OOM'd a 44GB GPU mid-epoch even with gradient
checkpointing on. `train_linking.py --max-mentions` (default 700) now skips
those outlier cases from training — cost is ~6 of 10,031 cases, well worth it
for a training run that can't be crashed by a single consolidated opinion.

### Rounds 3–4: what is actually wrong with `Id.` resolution (2026-09-15/16)

Round 2's linker had one weak category — `id` attach 0.894 vs 0.97–1.0 for
every other kind — so two single-variable linking-only runs tested the obvious
explanations. Both were negative on `id`, and the error analysis that followed
found the real cause.

**Round 3 — inject untagged `Id.`/`Ibid.`/`supra` as no-antecedent mentions**
(48,042 phantoms, 0.71 per real `Id.`), so "refuse to link" becomes a trained
answer (the eyecite #348/#349 failure class). Result: B³ 0.962 (−0.007),
**`id` attach 0.808 (−0.087)**, precision up / recall down — over-refusal. A
phantom `Id.` is lexically identical to a real one, and the distinguishing
context was truncated: **79.6% of round-2 windows hit the `lwin=64` cap**, so
the model could not tell them apart and hedged toward dummy. Conclusion:
non-case `Id.`s are a rule (the correction prompt already removes them as
`noncase_id`), not a learning problem.

**Round 4 — widen the window** (`lwin` 64→128 at `ctx` 128; truncation
79.6%→0.3%), no dummies. Training a wider window OOM'd first (38.8 GiB: slicing
the encoder pass bounds peak per call but autograd retains every slice until
backward), fixed by recomputing each slice in backward
(`AntecedentLinker(recompute=True)`, `--chunk-checkpoint`) — peak memory is now
independent of case size and window width. Result:

| | isolated B³ F1 (gold mentions) | end-to-end coref F1 (triage scorer) | `id` attach |
|---|---|---|---|
| round-2 warm | 0.969 | 0.961 | 0.894 |
| round 3 (dummies) | 0.962 | — | 0.808 |
| **round 4 (lwin 128)** | **0.974** | **0.977** | 0.894 |
| **round 4 + `Id.`→nearest decode rule** | **0.984** | **0.985** | **0.955** |
| eyecite | — | 0.975 | — |

**Round 4 is the new best linker, and the encoder now beats eyecite on
coreference too** (0.977 vs 0.975 end to end; extraction 0.973 vs 0.857). But
`id` attach did not move by a single mention (279/312 in both) — the gain is
cleaner failures (wrong links go to a new singleton instead of contaminating
a cluster), which is why it shows up more end to end (+0.016) than isolated
(+0.005). Context starvation is ruled out as the `Id.` explanation.

**The 33 `Id.`s round 4 still gets wrong** (`data/output/r4_id_failures.json`):

| failure | n |
|---|---|
| gold antecedent IS the nearest mention; model picked one further back | 17 |
| took the nearest; gold is further back (#348 shape, 4 inside a `(citing …)` paren) | 8 |
| wrong non-nearest candidate | 5 |
| refused (dummy) — all three had gold at slot 1 | 3 |

20 of 33 had the right answer sitting at slot 1 with `is_id=1, is_nearest=1`,
and the model reached past it — often 100+ mentions back — to a distant
look-alike. **The linker under-trusts adjacency for `Id.`, the opposite of
eyecite's failure.** Across gold_dev the nearest mention is the true antecedent
for **294/312 = 94.2%** of `Id.`s, and of the model's 279 correct attaches 102
picked a non-nearest candidate — but only 5 of those needed to: 97 were right
by luck, via union-find over a cluster dense enough to hold the distant pick.
So the fix is a decode rule, not more training: an `Id.`/`Ibid.` is forced to
the mention immediately before it, except when that mention sits inside a
`(citing …)`/`(quoting …)`/`(see …)` parenthetical that closed before the
`Id.` — then it skips past the parenthetical to the case it modifies (the
eyecite #348 correction). `LinkDataset.id_rule_slots` computes the slot per
mention, `AntecedentLinker.predict_clusters` honours it (`id_rule=False` /
`inference.py --no-id-rule` to score the raw model). Measured on the
round-4 linker: rule fired on all 312 gold_dev `Id.`s (302 nearest, 10
parenthetical skips); **`id` attach 0.894 → 0.955** (298/312 — the +20/−5
prediction plus 4 rescued by the exception), isolated B³ 0.974 → 0.984,
**end-to-end coref F1 0.977 → 0.985**. `manual` (+0.012) and `eyecite`
(+0.010) attach improved too — a correctly placed `Id.` pulls the rest of its
chain in behind it — and no category moved down. Everything the model is
actually good at (`name` 0.995, `reporter` 0.976, `supra` 1.0) stays with the
model; `Id.` adjacency is enforced. 14 `Id.` errors remain
(`data/output/r4_idrule_failures.json`), and they are structural too:

| cause | n |
|---|---|
| nearest mention is a case named *inside a quotation*; the `Id.` refers to the quoted source | 3 |
| `cert. denied` / subsequent-history cites sit between the main cite and the `Id.` | 2 |
| paren-skip over-fired — the author kept discussing the parenthetical's case (`Id.` pin equals that cite's pin every time) | 3 |
| `See also` supporting cite between the main case and the `Id.` (2 of 3 resolvable by pin cite) | 3 |
| parenthetical signal word beyond the 60-char lookback | 1 |
| probable gold error (145739: same volume, contiguous pages, same prose thread) | 1 |
| genuinely ambiguous | 1 |

Pin-cite plausibility (pin ≥ start page, small gap, exact match wins) resolves 5
of these on its own; quotation-as-parenthetical, a `cert. denied` skip and
balanced-paren lookback resolve 6 more — ~11/14, `id` attach ≈ 0.99, still
without training. `manual` (mentions eyecite never tagged, added by the
correction pass — mostly name-only, hence no `reporter_match` feature) is the
lowest remaining attach category at 0.974.

### Held-out test (gold_test, scored once, 2026-09-16)

gold_test — 50 opinions, 3,854 gold mentions — was untouched through rounds
1–4 and scored once, on request, after the round-4 checkpoint and the `Id.`
rule were fixed on dev. Same triage scorer, same current annotator gold for
every row. GPT-5.6 Luna was run for this on Bedrock Converse
(`runners/openai_batch.py run --provider bedrock`; 50/50 completed in 442 s,
no output-cap hits, ≈ $1); Opus is the frozen v4 test run re-scored against
the current gold.

| system (test, 50 opinions, 3,854 gold mentions) | mention P | mention R | mention F1 | coref P | coref R | coref F1 |
|---|---|---|---|---|---|---|
| eyecite (floor) | 0.895 | 0.827 | 0.860 | 0.996 | 0.935 | 0.964 |
| encoder r2 warm (lwin 64) | 0.956 | 0.963 | 0.960 | 0.947 | 0.937 | 0.942 |
| encoder r4 (lwin 128, no rule) | 0.956 | 0.963 | 0.960 | 0.958 | 0.928 | 0.943 |
| **encoder r4 + `Id.`→nearest rule** | 0.956 | 0.963 | **0.960** | 0.966 | 0.952 | **0.959** |
| GPT-5.6 Luna v8g + candidates, high (Bedrock Converse) | 0.968 | 0.940 | 0.954 | 0.995 | 0.993 | **0.994** |
| Opus 5 v4 (frozen 2026-09-10 outputs, re-scored) | 0.993 | 0.973 | **0.983** | 0.988 | 0.981 | 0.984 |

Isolated linking on test (gold mentions given): B³ F1 r2 warm 0.957 → r4 0.957
→ **r4 + rule 0.967**; `Id.` attach 0.901 → 0.911 → **0.944**. The rule helps
the round-2 linker just as much (0.901 → 0.947), so it is not fitted to one
checkpoint.

**What generalized and what did not.** Extraction transferred cleanly: +10
points over eyecite on test (0.960 vs 0.860; dev +11.6), and the encoder now
*edges GPT-5.6 Luna* on held-out extraction (0.960 vs 0.954; dev was a tie).
The `Id.` rule transferred (+0.016 coref end to end, +0.033 `Id.` attach). The
coref *level* did not: every linker dropped ~0.016 B³ from dev to test —
`supra` attach fell from 1.000 to 0.971 and the test opinions are longer — so
r4 + rule lands at 0.959, 0.005 under eyecite (dev: 0.010 over) and 3.5 points
under GPT v8g, which inherits eyecite's grouping and only edits it. Caveats
that cut both ways: the test gold is less reconciled than dev (only one-sided
auto-decisions; 41 review items open), which under-tags name-only mentions and
so penalizes the systems that find them (GPT's test edit precision is 0.911 vs
0.934 on dev for the same reason); and the window and rule were chosen on dev.

### Round 5 preparation: what eyecite still knows that the linker does not (2026-09-18)

Two offline studies on dev (no training, no model runs) to decide how to close
the held-out coref gap (encoder 0.959 vs eyecite 0.964 vs GPT 0.994):

**Decode rules on the round-4 output** (`experiments_09022026/triage/analysis/decode_rules_dev.py`,
merge-only rules bolted onto the encoder's end-to-end clusters, seeding scorer, 49 dev opinions):

| variant | mention F1 | coref P / R / F1 | merges (harmful) |
|---|---|---|---|
| eyecite (untouched seed) | 0.857 | 0.996 / 0.955 / 0.975 | |
| encoder r4 + `Id.` rule (as is) | 0.973 | 0.984 / 0.986 / 0.985 | |
| + reporter key (same volume / reporter / first page) | 0.973 | 0.984 / 0.990 / **0.987** | 14 (0) |
| + reporter key + short form (`805 F.2d at 1124` → unique full cite) | 0.973 | 0.984 / 0.991 / 0.987 | 16 (0), 2 ambiguous skipped |
| + eyecite groups, full reporter cites only | 0.973 | 0.984 / 0.991 / 0.987 | 16 (0) |
| + eyecite groups, all spans incl. `Id.`/supra | 0.973 | 0.922 / 0.995 / 0.957 | 31 (**9**) |

The reporter-key rule is free precision-neutral recall (+0.002 coref F1) and is
worth adopting; eyecite's own *full-cite* grouping gives the same two points,
so the two are interchangeable. Importing eyecite's `Id.`/short-form
resolution is what hurts (8 of the 9 harmful merges are `Id.` attachments in
one opinion) — exactly the part the gold corrected. Dev headroom for rules is
small because dev coref is already 0.985; the real gap sits on longer held-out
opinions, which cannot be re-measured (gold_test spent).

**How good is eyecite's grouping as an input signal?** Joined onto the corrected
labels (`mention_features.py --payload-dir`, column `eyecite_gid`): two
mentions eyecite put in one group are in the same gold group 99.6% of the time
on dev (8,135 / 8,167 pairs) and 99.5% on validation; eyecite joins 95.6% (dev) /
96.3% (validation) of the gold pairs among the mentions it tagged. Coverage: 85%
of training mentions carry an eyecite group (the rest are GPT-added name-only
mentions, `Id.`s and short forms eyecite missed). A near-perfect, high-coverage
channel the linker currently never sees.

**Round 5 = the edit-formulated linker, built and unit-tested, not yet run.**
The round-4 linker gets eyecite's grouping as three extra pair features
(eyecite grouped the pair together / apart / tagged only one), the pair scorer
is warm-started from r4 with zero columns for the new features (step-0 output
is bit-identical to r4, verified), and the channel is masked on 20% of
training cases so the model still works with no eyecite in front of it
(`best/coref_test_noeye.json` reports that condition). `EYE=0 MODE=r5link` trains
the identical control (r4 + the same two extra epochs, no channel), so the
delta is the channel and not the extra training. Judged on dev + validation only;
a fresh gold split is needed before any held-out claim.

### Round 5 result: the channel is learned but changes nothing on dev (2026-09-18, pod run, control pending)

`MODE=r5link` ran to completion (10,025 train cases, 2 epochs, 3 h 16 min, warm
start from r4 with the 3 zero-padded columns). Isolated coref (gold mentions,
`eval_coref` with the `Id.` rule on — note the r4 `coref_*.json` files on disk
predate the rule; the apples-to-apples r4 numbers are the `_idrule_isolated`
ones):

| isolated B³ F1 | validation (214 cases) | gold_dev (49 cases) | `id` attach dev | `reporter` attach dev |
|---|---|---|---|---|
| r4, no rule (as trained) | 0.974 | 0.974 | 0.894 | 0.976 |
| r4 + `Id.` rule | not measured | **0.984** | 0.955 | 0.976 |
| **r5 (+ eyecite channel) + rule** | **0.979** | 0.981 | 0.955 | 0.975 |
| r5 with the channel masked | not measured | 0.981 (identical to the mention) | 0.955 | 0.975 |

The linker did learn the channel — the "eyecite grouped them together" and
"apart" columns ended with weight norms 0.12 / 0.10, in the range of the
existing legal features (`is_id` 0.16, nearest 0.58, distance 0.62) — but on
gold_dev the with-channel and without-channel decodes agree on every single
mention. The reporter-keyed pairs eyecite knows about are already linked
correctly by the r4 model (reporter attach 0.975 either way) and `Id.`s are
decided by the rule, so there is nothing left on dev for the channel to fix;
the small validation gain over r4 (0.974 → 0.979) is confounded with the rule
until r4 + rule is scored on validation. r5 is 0.003 under r4 + rule on gold_dev,
within run-to-run noise on 49 cases. **Decision 2026-09-18: the `EYE=0` control is
dropped** — the masked decode of the same weights already isolates the channel's
effect, and the shared columns drifted only 0.0003 from r4, so a control would
reproduce r4. **Queued, in this order, once the GPU is free
(`model/eval_isolated.py`, eval-only, minutes each):** (1) r5 masked vs
unmasked on validation (14,811 mentions — does the channel change any decision on a
larger, GPT-labeled set?); (2) r4 + rule on validation (how much of 0.974 → 0.979
was the rule?). Caveats on validation as judge: ≈6% label noise (GPT edit
precision 0.934) and it was r5's checkpoint-selection set. **Result (2026-09-19, laptop, `eval_isolated.py`):
on validation the channel-on and channel-masked decodes are identical to the
mention as well — B³ 0.9794, attach 9,200 / 9,333, every per-kind count equal,
across 14,811 mentions and 214 cases (`data/output/isolated_val_gpt_r5{,_noeye}.json`).
Eval 2 (r4 + rule on validation) was skipped as planned: with zero decisions
moved by the channel, the validation gain over the r4 file on disk is the `Id.`
rule. Verdict: round 5 is a clean negative. The linker learned small weights
on the channel (the signal is redundant with the reporter-match and name
features it already has) and never lets it flip an argmax. The held-out gap is
not in the pairs eyecite can key; keep r4 + rules as the linker.**

### Disagreement-targeted adjudication: seed only where GPT and the encoder disagree (2026-09-18)

Idea for round 3 of seeding: the round-2 labels are eyecite + GPT-5.6 edits
(edit precision 0.934 on dev, so ≈6% of the 99K GPT-added mentions are
wrong). Instead of paying Opus to re-seed whole opinions, send it only the
places where the encoder disagrees with that label. Measured on dev
(`triage/analysis/disagreement_dev.py`: GPT v8g state vs the r4 encoder vs gold, 49 opinions):

| | GPT false positives | GPT false negatives | coref pairs GPT gets wrong |
|---|---|---|---|
| caught by a GPT↔encoder disagreement | 60 / 73 (82%) | 67 / 94 (71%) | 101 / 230 (44%) |
| yield: flagged items where GPT is the wrong one | 130 / 246 (53%) | | 101 / 366 (28%) |

Flagged items are mostly name-only mentions (77 of 130 GPT errors caught) and
`Id.` tokens; the 13 false positives both systems share are unreachable this
way. A perfect adjudicator on the flagged items lifts GPT's dev mention F1
**0.972 → 0.993** (Opus's own level) and coref 0.991 → 0.995. Workload: 34 of 49
opinions have ≥1 disagreement, 4.3 windows per opinion, 17% of the text.

Caveat: the encoder was *trained* on the round-2 labels, so on the training
set it will disagree less than on dev (it partly memorised them); dev is the
optimistic bound. The honest selector is a cross-fitted encoder (train on
half, predict the other half — two extra pod runs). Tooling is built:
`inference.py --split train,validation` produces the encoder's
predictions over the training set, `triage/analysis/disagreement_select.py`
renders per-opinion Opus inputs (current tagging + a `<disagreements>` block),
`prompts/triage_citation_fixer_v5d.md` adds a mandatory per-line
`<adjudicate>` verdict before the normal v5 pass, and the existing
`bedrock_batch.sh export --inputs-dir` / `citation_seed.py apply --inputs-dir`
carry the results back into the round-2 overrides. Cost at Opus batch rates
(train_b averaged 26.9K in / 12.7K out per opinion ≈ $0.23): ≈ $1,400–1,600
if 60–70% of the 10K opinions are flagged and sent whole; the adjudication-first
output should shorten the response, and restricting to `encoder_only` /
`current_only` items (mention errors, 53% yield) or to opinions with ≥ 3
items cuts it further. Nothing has been run.

### Disagreement count on the round-2 training labels (2026-09-19, no LLM)

The production encoder (warm extractor + r4 linker + `Id.` rule) was run over
every round-2 record (11,766 records / 9,984 opinions, 11.5 h on the M4 Pro,
`pred_train_gpt_val_gpt_r4_rule.json`) and diffed against the eyecite+GPT
labels it was trained on (`triage/analysis/disagreement_select.py`, outputs in
`data/citation_seed_r2/inputs_adjudicate/`):

| | count | note |
|---|---|---|
| opinions with ≥ 1 disagreement | 7,024 / 9,984 (70%) | dev analysis had 34/49 (69%) — memorisation did not suppress disagreement |
| items | 131,256 (13.1 / opinion) | ≈ 4% are duplicate lines from repeated GPT `add`s of one span (selector now dedupes); ≈ 126K net |
| mention-level items (encoder-only + current-only) | 75,707 | ≈ 10.9% of the 695,534 labeled mentions are disputed |
| grouping items | 55,549 | reported once per pair, at the later mention |
| text inside ±400-char windows | 52.9M of 334.6M chars (15.8%) | ≈ 14.7M input tokens if only windows were sent |

By kind: grouping reporter 21.4K · encoder-only name 17.5K · encoder-only
reporter 16.8K · current-only name 13.7K · grouping name 12.3K · current-only
reporter 12.0K · grouping id 9.7K · the rest (id / supra) under 3K each. Two
patterns stand out in the samples: the encoder tags parallel reporter cites
eyecite and GPT both left untagged (`--- U.S. ----, 136 S.Ct. 2243`), and it
rejects many GPT name-only adds that sit directly in front of the case's own
citation (`Apprendi` in `Apprendi v. New Jersey, …`) — the fragment rule GPT
was told to apply. Both are the kinds that carried the 53% yield on dev.

Whole-opinion Opus batch cost by concentration (input at $2.50/M, output at
$12.50/M with the train_b average of 12.7K output tokens per opinion):

| send opinions with ≥ N items | opinions | share of items | est. cost |
|---|---|---|---|
| 1 | 7,024 | 100% | ≈ $1,300 |
| 5 | 4,215 | 95% | ≈ $820 |
| 10 | 2,936 | 89% | ≈ $590 |
| 20 | 1,794 | 77% | ≈ $370 |
| 50 | 679 | 51% | ≈ $150 |

Output tokens are 85% of the bill, so an adjudicate-only response (no full
`<scan>`/`<name_sweep>`) would cut it well below these figures; a window-only
input path is not built. **Decision pending**: whether the ≈ 71K
disputed mentions are worth an Opus pass, and at which tier. Costs are Bedrock
**batch** rates (half of on-demand; the Opus 5 tier itself is unverified — if it
bills at the Opus 4.1 tier, multiply by 3). To look at the disagreements:
the disagreement viewer (`uv run uvicorn app:app --port 8230`, then
`http://127.0.0.1:8230/?min=50`) shows each opinion's text with the eyecite
seed, the GPT-seeded label and the encoder output as three stripe layers,
click-to-inspect grouping per system, and the selector's item list. It is an
analysis tool rather than part of the pipeline, so it now sits unversioned in
`data/_archive/disagree_viewer/`.

## Final recommendation

**Extraction: the encoder.** `extract_large-caselaw_warm` beats eyecite by
10 points on held-out data and edges the GPT-5.6 correction pass it was trained
from, at GPU-inference cost (≈ $50–160 per million opinions) instead of
≈ $8K (GPT batch) or ≈ $200K (Opus). Adopt it as the production extractor.

**Coreference: not the encoder yet.** On held-out data the round-4 linker with
the `Id.` rule (0.959) is level with eyecite (0.964) and clearly behind the GPT
pass (0.994). Grouping is a mechanical problem for reporter-keyed mentions —
eyecite's near-perfect precision comes from exact volume/reporter/page keying,
and the LLM rows win by inheriting it. The next step is therefore not more
training but porting that keying into the decoder the way the `Id.` rule was
(same normalized reporter key → same cluster; learned scorer only for
name-only, `supra` and the residual), then an edit-formulated linker that
starts from eyecite's groups for the mentions eyecite found.


## Next steps

1. Round 5's missing control: `EYE=0 MODE=r5link` on the pod, the same warm
   start and data with no channel. The channel arm ran and was scored with the
   channel masked at decode; only a retrained control separates "the channel
   adds nothing" from "the extra columns cost nothing".
2. Adopt the reporter-key decode rule in `predict_clusters` (dev +0.002 coref,
   0 harmful merges); the remaining `Id.` rules (pin-cite plausibility,
   quotation-as-paren, `cert. denied` skip) after it.
3. Count the GPT-vs-encoder disagreements on the round-2 labels: encoder over
   `train,validation` on a pod (`make_predict_bundle.sh` / `run_predict_pod.sh`) →
   `disagreement_select.py` → read `stats.json`. The Opus adjudication pass
   (`triage_citation_fixer_v5d.md`, export/apply `--inputs-dir`) stays parked
   until that count says it is worth the spend.
4. Reconcile the 41 open test-review items so test gold stops under-tagging
   name-only mentions; carve a fresh held-out gold split (gold_test is spent).
5. Retry-or-drop decision on the 6 long round-2 opinions that hit GPT's output
   ceiling.

## How to reproduce

```bash
# 1. Sample (requires the CourtListener docker stack)
docker cp corpus/exclude_ids/exclude_cluster_ids_round2.csv cl-django:/opt/courtlistener/
docker cp corpus/sample_cl_20k_round2.py cl-django:/opt/courtlistener/
docker exec cl-django python manage.py shell -c "exec(open('sample_cl_20k_round2.py').read())"
docker cp cl-django:/opt/courtlistener/cl20k_r2/ ./data/

# 2. Annotator root + GPT seeding harness (see experiments_09022026/triage/docs/checklist.md
#    for the harness itself; this is the round-2-specific wiring)
python3 corpus/make_r2_annotator.py                # data/cl20k_r2/ -> data/annotator_r2/
cd ../experiments_09022026/triage
export CITSEED_ANNOT=$PWD/../../experiments_09092026/data/annotator_r2
export CITSEED_OUT=$PWD/../../experiments_09092026/data/citation_seed_r2
export OPENAI_KEY=...                       # conda env `citator` has it
python3 lib/citation_seed.py prepare --seed-state --ids $(cat $CITSEED_ANNOT/ids.txt)
bash runners/openai_batch.sh export --name r2_gpt --model gpt-5.6-luna \
    --prompt prompts/triage_citation_fixer_v8g.md --effort high --candidates \
    --max-tokens 128000 --ids $(cat $CITSEED_ANNOT/ids.txt)
bash runners/openai_batch.sh submit --name r2_gpt_p01   # … p02..p04; then status / fetch
python3 lib/citation_seed.py apply --write --ids <fetched ids> --out-dir $CITSEED_OUT/r2_gpt
cd ../../experiments_09092026

# 3. Labels -> dataset -> pod bundle
bash corpus/export_r2_revised_html.sh
uv run --no-project python corpus/build_dataset.py \
    --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \
    --gold-splits  ../experiments_09022026/triage/inputs/splits.csv \
    --llm-train-ids ../experiments_09022026/triage/data/citation_seed/train_seeded_ids.txt \
    --gpt-train-revised data/annotator_r2/data/revised_html --gpt-train-ids data/annotator_r2/ids_seeded.txt \
    --out data/output/citations_r2.jsonl --validate
uv run --no-project python corpus/mention_features.py --jsonl data/output/citations_r2.jsonl --out data/output/mention_features_r2.jsonl
R2=1 bash model/make_pod_bundle.sh        # -> r2_pod_bundle.tar.gz (2.9 GB, incl. round-1 checkpoints)

# 4. On the pod
tar xzf r2_pod_bundle.tar.gz && cd r2_pod/model
MODE=warm bash run_runpod.sh                 # -> runs/*_warm, r2_warm_runs.tar.gz
MODE=cold bash run_runpod.sh                 # control -> runs/*_cold, r2_cold_runs.tar.gz

# 5. Back on the laptop, score against the triage scorer
RUN_SUFFIX=_warm SILVER_RUNS=<extracted runs dir> uv run python model/inference.py --records data/output/gold_dev_fresh.jsonl
python3 ../experiments_09022026/triage/analysis/score_encoder.py --pred data/output/pred_gold_dev_warm.json \
    --ids $(cat ../experiments_09022026/triage/data/citation_seed/dev_ids.txt)
```

```bash
# 6. Round 5 (eyecite grouping as a linker input channel) — ran 2026-09-18; EYE=0 control did not
uv run --no-project python corpus/mention_features.py --jsonl data/output/citations_r2.jsonl \
    --payload-dir data/annotator_r2/data/opinion_html \
    --payload-dir ../experiments_09022026/triage/data/annotator/data/opinion_html \
    --out data/output/mention_features_r2e.jsonl          # + eyecite_gid (done; 85% of mentions carry one)
R5=1 bash model/make_pod_bundle.sh                     # r5_pod_bundle.tar.gz = r2 data + r2e features + r4 linker
# pod:  MODE=r5link bash run_runpod.sh ; EYE=0 MODE=r5link bash run_runpod.sh   (channel vs control)
RUN_SUFFIX=_warm LINK_SUFFIX=_r5 uv run python model/inference.py --eyecite-feats \
    --records data/output/gold_dev_fresh.jsonl [--no-eyecite-input]           # then score_encoder.py as above

# 7. Offline dev studies behind round 5 / the adjudication idea (pure python, seconds)
python3 ../experiments_09022026/triage/analysis/decode_rules_dev.py          # rules on the r4 output
python3 ../experiments_09022026/triage/analysis/disagreement_dev.py          # GPT vs encoder vs gold

# 8. Count GPT-vs-encoder disagreements on the round-2 labels (decided 2026-09-18: measure first,
#    Opus only if the count justifies it). Inference kit for a pod:
bash model/make_predict_bundle.sh                      # r4_predict_bundle.tar.gz = warm extractor + r4 linker + records
# pod:  tar xzf r4_predict_bundle.tar.gz && cd r4_predict/model
#       LIMIT=50 bash run_predict_pod.sh ; SPLIT=validation bash run_predict_pod.sh ; bash run_predict_pod.sh
#       runpodctl send pred_train_validation_r4_rule.tar.gz
# laptop: count and inspect the disagreements (no LLM involved)
cd ../experiments_09022026/triage
export CITSEED_ANNOT=$PWD/../../experiments_09092026/data/annotator_r2 CITSEED_OUT=$PWD/../../experiments_09092026/data/citation_seed_r2
python3 analysis/disagreement_select.py --pred ../../experiments_09092026/data/output/pred_train_validation_r4_rule.json
#   (the run already on disk predates the 2026-09-22 split rename: pred_train_gpt_val_gpt_r4_rule.json)
#   -> inputs_adjudicate/stats.json (items by kind, flagged opinions, share of text) + summary.csv (per opinion)
# only if the count says it is worth it — the Opus adjudication pass (built, parked):
#   bash runners/bedrock_batch.sh export --name adj_r3 --prompt prompts/triage_citation_fixer_v5d.md \
#       --inputs-dir $CITSEED_OUT/inputs_adjudicate --ids $(cat $CITSEED_OUT/inputs_adjudicate/ids_flagged.txt)
#   submit / wait / fetch ; python3 lib/citation_seed.py apply --inputs-dir $CITSEED_OUT/inputs_adjudicate --out-dir $CITSEED_OUT/adj_r3 --ids … --write
#   then corpus/export_r2_revised_html.sh → corpus/build_dataset.py → corpus/mention_features.py → bundle → retrain
```

`run_runpod.sh` picks batch size / gradient accumulation from available VRAM
automatically (`BATCH=auto`, keeps effective batch at 16); override with
`BATCH=<n> ACCUM=<n>` by hand. Requires `transformers<5` (pinned in
`model/pyproject.toml`; both trainers also work on 5.x via
`common.warmup_kwargs()`).

## Layout

```
corpus/         building the labelled corpus, in pipeline order (see corpus/README.md)
                  sample_cl_20k.py, sample_cl_20k_round2.py   CLReplica samplers (round 1 / round 2)
                  exclude_ids/*.csv                           never-sample lists, so the rounds do not overlap
                  make_r2_annotator.py, export_r2_revised_html.{py,sh}, r2_check.sh
                                                              round-2 seeding-harness wiring
                  build_dataset.py, mention_features.py       html_with_citations / revised_html -> records
                                                              (mention_features --payload-dir adds eyecite_gid,
                                                              the round-5 channel)
model/          training and inference for both models (see model/README.md)
                  train_extraction.py, train_linking.py, run_runpod.sh, make_pod_bundle.sh   training
                  inference.py, linking_model.py, linking_data.py, common.py, metrics.py     inference
                  make_predict_bundle.sh, run_predict_pod.sh  inference kit for a pod
                  eval_isolated.py                            isolated B³ of any linker on any split,
                                                              eyecite channel on or masked
results/        one folder per run: eval reports, trainer state, config (see results/README.md)
                  logs/                                       raw run stdout, gitignored
data/           gitignored: sampled blocks, built datasets, seeding state
runs/           gitignored: checkpoint landing zone, empty until weights are downloaded
```

The published weights and the published corpus live on Hugging Face;
`results/README.md` maps each run to its repository.
