# Citation extraction + coreference: all systems on one scorer (dev, 2026-09-11)

One table, one scorer, one gold. Every row is scored with
`experiments_09022026/triage/citation_seed.py score_states` — overlap-matched
mention precision/recall/F1 and pairwise coreference precision/recall/F1 over
the matched mentions — against the **current** annotator gold for the 49 triage
dev opinions (2,953 gold mentions). Section 4 explains what each row is, section
5 the prices, section 6 the caveats that matter when reading it, section 7 the
next round.

## 1. Results

| system | mention P | mention R | mention F1 | coref P | coref R | coref F1 |
|---|---|---|---|---|---|---|
| eyecite, as tagged by the annotator (floor) | 0.872 | 0.842 | 0.857 | 0.996 | 0.955 | 0.975 |
| silver encoder — CaseLawModernBERT-large trained on eyecite labels, extraction → linking end to end | 0.967 | 0.866 | 0.914 | 0.970 | 0.898 | 0.933 |
| Kimi K2.5, prompt v5 (48 of 49 opinions) | 0.883 | 0.834 | 0.858 | 0.985 | 0.983 | 0.984 |
| GPT-5.6 Luna, prompt v5, medium effort | 0.955 | 0.888 | 0.920 | 0.991 | 0.990 | 0.990 |
| Sonnet 4.6, prompt v5 | 0.967 | 0.900 | 0.932 | 0.992 | 0.993 | 0.992 |
| GPT-5.6 Luna, prompt v8g + candidates + post-filter, high effort | 0.975 | 0.968 | 0.972 | 0.987 | 0.994 | 0.991 |
| Opus 5, prompt v5 | 0.991 | 0.995 | 0.993 | 0.988 | 0.997 | 0.993 |

Edit precision (share of a model's edits to eyecite's output that gold agrees
with) for the LLM rows: Opus 0.974 · GPT v8g-high 0.934 · Sonnet 0.889 ·
GPT v5-medium 0.855 · Kimi 0.573. Not defined for the encoder (it does not
edit eyecite's output).

## 2. What the numbers mean

- **Mention P/R/F1**: a system mention counts as correct when it overlaps a
  gold mention in the same writing (greedy one-to-one matching). Recall is the
  number to watch: it is where the systems differ, and it is dominated by
  *name-only* references (`In Pugliese, we held…`, `the LiMandri factors`) that
  eyecite does not produce.
- **Coref P/R/F1**: pairwise — over the matched mentions, every pair is
  judged same-group vs different-group against gold. Because it is computed on
  each system's own matched mentions, a system that finds fewer mentions is
  judged on fewer pairs; it is not conditioned on a shared mention set.
- eyecite is the floor every LLM row *edits*: the LLM sees eyecite's tags and
  groups and emits remove/regroup/merge/add edits. The encoder row is the only
  one that re-extracts from raw text.

## 3. Reading the table

- **Only Opus clears the 0.99 gate** set for the dev loop (mention and coref
  F1 ≥ 0.99). It recovers 455 of the 472 gold additions to eyecite's output.
- **GPT-5.6 with the v8g pipeline is the best cheap option** at 0.972 mention
  F1, 2 points below Opus for roughly 1/25 of the price. The gain over the
  baseline GPT row came from three things in order of effect: reasoning effort
  high (+3.2 F1), a deterministic candidate generator that finds the name /
  `Id.` / short-form occurrences for the model to adjudicate (+1.2), and
  prompt fixes that removed whole error classes (wrong supra adds, wrong
  citation removes). Prompt wording alone at medium effort moved F1 by < 0.01.
- **Coreference is solved by every LLM** (0.98–0.99). All LLM rows start from
  eyecite's grouping at 0.975 and fix `Id.` chains; the differences are noise.
- **Sonnet 4.6 and Kimi are not competitive on recall** (0.900 / 0.834): they
  apply the conventions when they see a candidate but do not find the
  candidates. Kimi is at the eyecite floor with a large volume of wrong edits.
- **The silver encoder is a precision story.** Trained only on eyecite's
  labels it reproduces eyecite's recall ceiling (0.866; it cannot know the
  mentions eyecite never produced) but it learned *not* to tag statutes,
  regulations and record cites — 0.967 precision vs eyecite's 0.872, and only
  5 of 158 curator-removed spans tagged in the pod's own eval. Its coref (0.933)
  is the lowest row because the linker clusters its own extraction from
  scratch rather than editing eyecite's near-perfect grouping.

## 4. Specs per row

| row | model id | runner | prompt | reasoning | max output | other |
|---|---|---|---|---|---|---|
| Opus 5 | `us.anthropic.claude-opus-5` | Bedrock batch (`triage/bedrock_batch.sh`) | `prompts/triage_citation_fixer_v5.md` | adaptive thinking | 128,000 (model ceiling) | 48 dev from job `train_b`, 809122 from `opus_retry` |
| Sonnet 4.6 | `us.anthropic.claude-sonnet-4-6` | Bedrock batch | v5 | thinking, 8K budget | 128,000 | Sonnet 5 is not batch-enabled on Bedrock us-west-2 |
| Kimi K2.5 | `moonshotai.kimi-k2.5` | Bedrock batch, OpenAI-chat body, prompt prepended to the user turn | v5 | none, temperature 0 | 128,000 (shares the 262K context with the input) | 1 record looped to the cap |
| GPT-5.6 v5 | `gpt-5.6-luna` | OpenAI real-time (`triage/openai_batch.sh run`, 8 concurrent) | v5 | `reasoning_effort=medium` | 128,000 | 190 s for 49 |
| GPT-5.6 v8g | `gpt-5.6-luna` | OpenAI real-time, 8 concurrent, `--candidates` | `prompts/triage_citation_fixer_v8g.md` | `reasoning_effort=high` | 128,000 | `candidates.py` block appended to the input; `postfilter_adds` drops name fragments / duplicates; 564 s for 49 |
| silver encoder | `ai-law-society-lab/CaseLawModernBERT-large`, two heads | `experiments_09092026/finetune/train_extraction.py` + `train_linking.py` | – | – | – | 13,304 silver train clusters, 3 epochs each (11.1 h + 3.3 h on one 44 GB GPU); scored via `finetune/predict_gold_dev.py` → `triage/score_encoder.py` |

Inputs for every LLM row: `triage/data/citation_seed/inputs/{cid}.txt` (citing
case header, cited-case inventory, opinion with eyecite tags) prepared by
`citation_seed.py prepare --seed-state`.

## 5. Prices

Measured tokens are from the runs above (49 dev opinions). List prices are the
per-million on-demand rates used; Bedrock batch and the OpenAI Batch API are
half of on-demand. Opus 5's Bedrock list price was not verified — the Opus 4.5
tier ($5 / $25) is assumed; if it is priced at the Opus 4.1 tier ($15 / $75)
multiply the Opus figures by 3.

| system | list price in / out (per M) | measured tokens per opinion (in / out) | cost per 49 dev | per opinion | 20,000 opinions |
|---|---|---|---|---|---|
| Opus 5, batch | $2.50 / $12.50 (batch) | 26.9K / 12.7K (train_b, incl. giants) | ≈ $8 | ≈ $0.20 | ≈ $4,000 |
| Sonnet 4.6, batch | $1.50 / $7.50 (batch) | 14.5K / 6.6K | ≈ $5 | ≈ $0.07 | ≈ $1,400 |
| Kimi K2.5, batch | ≈ $0.30 / $1.25 (batch) | 13.2K / 9.8K | ≈ $1 | ≈ $0.02 | ≈ $400 |
| GPT-5.6, v5 medium, real-time | $0.20 / $1.20 | 13.4K / 3.0K (1.7K reasoning) | $0.31 | $0.006 | ≈ $130 |
| **GPT-5.6, v8g high, real-time** | $0.20 / $1.20 | 16.6K / 9.6K (≈ 7K reasoning) | **$0.73** | **$0.015** | **≈ $300 real-time / ≈ $150 batch** |

The round-2 set was sized down to **10,000** opinions on 2026-09-11: halve the
last column (GPT v8g ≈ $150 real-time / ≈ $75 batch; Opus ≈ $2,000).

Throughput for the GPT v8g pipeline: 564 s per 49 opinions at 8 concurrent
requests ≈ 11.5 s per opinion per 8 workers — 20,000 opinions ≈ 64 h at 8
workers, ≈ 16 h at 32 workers (rate limits permitting; the runner retries on
429). The OpenAI Batch API halves the cost but the one batch tried sat at
0/49 completed for 2.5 h before it was cancelled; for 20K the batch route is
worth retrying since turnaround matters less than for a dev iteration.

Compute so far: 215 Opus opinions ≈ $43 in batch; the GPT hardening rounds
≈ $3 in total.

## 6. Caveats

1. **Gold reconciliation.** The dev gold was corrected through the
   seed-vs-gold review: Rachel decided 149 disagreements from dev iterations
   1–2 and 65 from iteration 3, taking the LLM's side on ~80% of name-only
   additions. Those corrections were made against Opus outputs, so the Opus
   row benefits from a gold that has been reconciled with it; the other rows
   were scored on the resulting gold without their own review pass. This
   inflates Opus's lead by an unknown but real amount; it does not change the
   ordering of the other rows relative to each other.
2. **Dev only.** These are dev numbers; the prompt was iterated on them. The
   frozen-prompt test number (Opus v4, 50 test opinions, unreviewed gold) was
   mention F1 0.954 / coref 0.979; after the one-sided auto-decisions on test
   it is 0.983 / 0.984 with 41 items still open for Rachel. No other model has
   been run on test.
3. **Kimi scored on 48 of 49** (one record degenerated into a repetition
   loop). Opus's 49th opinion (809122) came from a second job with a larger
   output cap.
4. **The encoder's pod eval was on 29 clusters.** 20 dev `revised_html`
   files had been zeroed by a viewer bug (the seed-review endpoint crashed
   after opening the file for writing) before the pod bundle was built on
   2026-09-10, so the pod's exact-span eval (F1 0.870) covered 29 clusters.
   The row above is on all 49, computed 2026-09-11 after regenerating the
   files; the two metrics are also different (exact vs overlap match).
5. **Encoder coref is not like-for-like with the LLM coref.** The linker
   clusters the encoder's own predicted mentions; the LLMs edit eyecite's
   grouping (already 0.975). With gold mentions given, the pod measured the
   linker at B³ 0.947.
6. **eyecite here is the annotator's eyecite 2.7.8 pass**, not CourtListener's
   stored `html_with_citations`; the two differ slightly (the encoder was
   trained on the latter).
7. **Prices are estimates.** Bedrock list price for Opus 5 unverified; GPT
   reasoning-token counts vary with opinion length; the 20K projections scale
   dev's per-opinion tokens by the round-1 sample's mean text length (27K
   chars, similar to dev's), so long-opinion-heavy cells will cost more.

## 7. Next round: 10K GPT-seeded training set (sized down from 20K on 2026-09-11)

**Sampling** — `experiments_09092026/sample_cl_20k_round2.py` (Rachel runs it
in the `cl-django` container; commands in its docstring). Same 135 cells as
round 1 (5 court groups × 9 decades × 3 length bins), but each cell takes 60
*keyword* + 15 *control* clusters, where keyword = at least one strong
citing-reference term (overrule, abrogate, distinguish, decline to follow, not
persuasive, … — the exact regex list from `triage/sample_clreplica.py`) within
300 characters of an eyecite citation span; direct-history words do not
qualify. That mirrors the triage training set's oversampling (5:1 there,
4:1 here). Exclusion list `inputs/exclude_cluster_ids_round2.csv` = 20,613
ids: benchmark (383), triage annotator clusters (422), round-1 20K (19,260)
and the round-1 exclusion pool (1,353) — so the new set overlaps nothing
trained or evaluated on. Expect several hours (round 1 scanned 63K candidates
in 78 min; keyword opinions are a minority, so this scans more).

**Seeding** — GPT-5.6 Luna with the v8g pipeline (candidates block, post-filter,
reasoning effort high), the best cheap configuration above:
`bash openai_batch.sh run --name r2_gpt --model gpt-5.6-luna --prompt prompts/triage_citation_fixer_v8g.md --effort high --candidates --workers 32 --ids …`
after `citation_seed.py prepare --seed-state` on the new clusters (10K → ≈ $150 real-time / ≈ $75 batch, ≈ 8 h at 32 workers) (clusters live in
`experiments_09092026/data/annotator_r2/`, built by `make_r2_annotator.py`;
the harness is pointed at it with `CITSEED_ANNOT` / `CITSEED_OUT`). ≈ $300
real-time or ≈ $150 via the Batch API.

**Warm or cold start?** Warm-start from the silver checkpoints
(`runs/extract_large-caselaw/best`, `runs/link_large-caselaw/best`), and run a
cold-start control if the GPU time is available. Reasons: the silver model
already learned the span-tagging task, CL's text distribution and the
precision behaviour we want to keep (no statutes/records); the GPT labels are
a superset of the silver labels (eyecite's spans plus the corrections), so
fine-tuning moves it toward recall without unlearning precision; and 11 h of
cold-start compute becomes ~2 epochs at a lower learning rate (1e-5). The risk
is inheriting the silver model's under-recall on name-only mentions — 10K
examples that contain them is a strong signal against that, but the
cold-start control is what proves it. Selection on gold_dev, the triage scorer
as the headline metric (via `predict_gold_dev.py` → `score_encoder.py`),
gold_test once at the end.

**Training mix (as built 2026-09-14)** — the 9,994 GPT-seeded clusters as
gold-format records (split `train_gpt`, with 2% of clusters by hash held out as
`val_gpt` for checkpoint selection on same-distribution labels) plus the 261
triage train clusters carrying Opus v5 corrections (split `train_llm`), with
gold_dev / gold_test as before; no silver blocks (the warm start carries them,
the cold control trains on the same GPT data). Dataset sizes, commands and the
pod package: `experiments_09092026/readme.md` §6.

**Label fixes applied 2026-09-11** — (a) all 49 dev, 50 test and 323 train
`revised_html` files regenerated server-side from the current overrides (20
dev files had been zeroed; of the 45 train files that existed, 39 predated
their overrides and 216 seeded train clusters had none); (b)
`build_dataset.py` now refuses zero-byte gold HTML instead of emitting empty
records and can emit the LLM-corrected train clusters as `train_llm`; (c) the
viewer endpoint that caused the truncation is fixed. The round-2 dataset was
rebuilt on these labels on 2026-09-14 (`build_dataset.py --gpt-train-*`, see
`experiments_09092026/readme.md` §6).
