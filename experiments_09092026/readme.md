# experiments_09092026 — citation extraction + coreference encoder on CaseLawModernBERT-large

Train the citation encoder (Task A extraction: BIO over case-citation spans;
Task B coreference: antecedent-ranking linker) on CLReplica opinions and
measure against the human-verified dev set of experiments_09022026 (49
clusters). Two rounds:

- **Round 1 (silver, §1–5):** ~20,000 opinions whose labels come straight from
  eyecite (`html_with_citations`) — how far does eyecite supervision alone get a
  continue-pretrained ModernBERT-large? Answer: it inherits eyecite's recall
  ceiling but sheds its precision failures (§5).
- **Round 2 (GPT-seeded, §6):** 10,000 new opinions oversampled for negative
  treatment language, citation grouping corrected by GPT-5.6 Luna (prompt v8g)
  through the 0902 seeding harness, then a warm-start fine-tune from the round-1
  checkpoints with a cold-start control. Package built 2026-09-14; pod run pending.

Harness = experiments_06142026/finetune (Exp 3) ported: tag-based splits instead
of CV folds, `large-caselaw` backbone (`ai-law-society-lab/CaseLawModernBERT-large`,
public on HF), step-based eval for the bigger corpus.

## 1. Sample 20K opinions from CLReplica (Rachel runs this)

Requires the CourtListener docker stack up (`cl-django` container). One-off
inputs: `inputs/exclude_cluster_ids.csv` = benchmark citing clusters + every
cluster the triage experiment sampled (1,353 ids) — never selected.

```bash
cd ~/Desktop/flp/ai-research/experiments_09092026
docker cp inputs/exclude_cluster_ids.csv cl-django:/opt/courtlistener/
docker cp sample_cl_20k.py cl-django:/opt/courtlistener/
docker exec cl-django python manage.py shell -c "exec(open('sample_cl_20k.py').read())"
#   … prints one line per (court group, decade) block; resumable — re-run to continue
docker cp cl-django:/opt/courtlistener/cl20k/ ./data/
```

Output `data/cl20k/`: `blocks/{group}_{decade}.jsonl.gz` (html per selected
cluster), `sample_metadata.csv`, `cell_fill.csv`, `summary.json`. Strata: 5 court
groups × 9 decades × 3 length bins, 150 per cell + overflow top-up to 20,000;
requires Published status, `html_with_citations` on every writing, ≥3 eyecite
spans, 3K–150K chars. Expect a few hours (each block scans up to 1,400 random
clusters and fetches html in batches of 50).

If the container path differs, edit `OUT`/`EXCLUDE_CSV` at the top of the script;
to change the total, `TARGET_TOTAL` / `TARGET_PER_CELL`.

## 2. Build the dataset locally

```bash
uv run --no-project python build_dataset.py --blocks "data/cl20k/blocks/*.jsonl.gz" \
    --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \
    --gold-splits  ../experiments_09022026/triage/inputs/splits.csv --validate
python3 mention_features.py
```

→ `data/output/citations.jsonl` (records tagged train / val / gold_dev /
gold_test; val = 2% of silver clusters by hash) + `mention_features.jsonl` +
`stats.json`. Non-case eyecite spans (§, U.S.C., C.F.R., Stat., L. Rev., …) are
dropped from silver; unresolved spans are singleton clusters. Gold records come
from the triage annotator's `revised_html` (`<section data-opinion-type>` /
`<cite group cited_ref change>` / `<noncite>`); zero-byte gold files are refused.
Other splits: `--llm-train-ids` → `train_llm` (Opus-corrected triage train
clusters), `--gpt-train-revised` + `--gpt-train-ids` → `train_gpt` / `val_gpt`
(round 2, §6). The stats file is named after the output (`citations_r2.jsonl` →
`stats_r2.json`).

## 3. Pod package

```bash
bash finetune/make_pod_bundle.sh          # -> silver_pod_bundle.tar.gz (code + data, no weights)
runpodctl send silver_pod_bundle.tar.gz   # paste the receive command on the pod
```

On the pod (A100/H100 80GB; 48GB works with `--batch 1 --grad-accum 16`):

```bash
runpodctl receive <code>
tar xzf silver_pod_bundle.tar.gz && cd silver_pod/finetune
MAX_TRAIN=2000 bash run_runpod.sh         # pilot: 2,000 clusters, both tasks
bash run_runpod.sh                        # full: all training clusters
runpodctl send silver_runs.tar.gz         # back on the laptop: runpodctl receive <code>
```

`run_runpod.sh` runs Task A then Task B; `MODE=silver` (default) trains on
`train`, selects on `val`; `MODE=warm` / `MODE=cold` are the round-2 settings
(§6). Any mode default can be overridden by env (`TRAIN_SPLIT VAL_SPLIT EPOCHS
LR_A LR_B INIT_A INIT_B SUFFIX`).

**transformers version (fixed 2026-09-10).** The first pod smoke pulled
transformers 5.x, which removed `TrainingArguments.warmup_ratio` and made both
training scripts fail at startup. `run_runpod.sh` and `pyproject.toml` pin
`transformers<5` (4.57 is what the local smoke ran on), and both scripts go
through `common.warmup_kwargs()` so they also work on 5.x. To patch a pod that
already has the old bundle without resending the data:

```bash
# laptop (experiment root)
tar czf finetune_fix.tar.gz -C finetune common.py train_extraction.py train_linking.py run_runpod.sh pyproject.toml
runpodctl send finetune_fix.tar.gz
# pod
runpodctl receive <code> && tar xzf finetune_fix.tar.gz -C silver_pod/finetune
cd silver_pod/finetune && MAX_TRAIN=20 bash run_runpod.sh
```

The `UNEXPECTED ... can be ignored when loading from different task/architecture`
line at load time is transformers 5's loading report for the unused MLM head; it
is harmless and disappears under 4.x.

**VRAM use / batch size.** `run_runpod.sh` defaults to `BATCH=auto`: it reads
total VRAM and picks the extraction batch, gradient accumulation and
checkpointing from a table, always keeping the effective batch at 16 so the
optimizer schedule is identical across cards (the model + Adam states are ~7GB;
the old fixed `--batch 2 --grad-accum 8 --grad-checkpoint` used ~20% of 80GB).

| total VRAM | batch | accum | grad checkpointing |
|---|---|---|---|
| ≥ 75 GB (A100/H100 80GB) | 8 | 2 | off (~30% faster) |
| ≥ 40 GB (A6000/L40S 48GB) | 4 | 4 | off |
| ≥ 22 GB (24GB cards) | 2 | 8 | on |
| smaller | 1 | 16 | on |

Measured on a 44GB card (2026-09-10): batch 4 + checkpointing on used ~10.5GB
(optimizer state ~9GB, checkpointed activations <1GB), so checkpointing is the
lever there, not batch size; without it expect ~2-3GB per 4096-token sample.
Override by hand with `BATCH=16 ACCUM=1 CKPT=1 bash run_runpod.sh` (ACCUM
defaults to `16/BATCH`). Why not the Trainer's `auto_find_batch_size`: it only
halves the batch on OOM and does not rescale accumulation, so the effective
batch (and results) would drift between cards.

Linking (Task B) trains one case per step by design (`per_device_train_batch_size=1`,
a case's mentions all attend to each other), so its VRAM stays low regardless;
only `--grad-accum` applies there.

Results: `runs/<name>/eval_test.json` (gold_dev span P/R/F1, `manual_recall` =
recall on curator-added mentions eyecite missed, `neg_tagged_rate` = how often
curator-removed spans get tagged) and `runs/<name>/best/coref_test.json`
(gold_dev B³ + attachment). Floors to beat on the same gold: eyecite itself
(`experiments_09022026/triage/citation_seed.py score` baseline rows) and Exp 3's
tiny-set numbers (extraction F1 0.74 large, coref B³ ~0.61).

## 4. Smoke (done 2026-09-09)

Built from the triage sample (512 silver records / 39.6K mentions, gold_dev 68
records / 2,896 mentions / 352 negatives, gold_test 72 / 3,720); both trainers
ran 2 CPU steps on `answerdotai/ModernBERT-base`.

## 5. Results — full silver run on CaseLawModernBERT-large (fetched 2026-09-11)

Pod run (`bash run_runpod.sh`, transformers 4.x, extraction batch 4 × accum 4 with
checkpointing on a 44 GB card): **13,304 train / 281 val clusters (26,911 / 572
windows), 3 epochs, 11.1 h** for extraction; linking 13,304 train cases, 3 epochs,
3.3 h. Eval = the 49 gold_dev clusters (60 sub-opinion records, 1,067 gold
mentions: 956 that eyecite also found + 111 curator-added; 158 curator-removed
spans as negatives). gold_test untouched.

### Task A — extraction (exact char-span match, `runs/extract_large-caselaw/eval_test.json`)

| | span P | span R | span F1 | recall on curator-added (eyecite-missed) | curator-removed spans still tagged |
|---|---|---|---|---|---|
| eyecite (floor, same metric, from the gold records' `source` tags) | 0.858 | 0.896 | 0.877 | 0.000 | 1.000 (158/158) |
| **silver model** | **0.911** | 0.833 | 0.870 | 0.090 (10/111) | **0.032** (5/158) |
| silver val (model vs eyecite labels) | 0.937 | 0.953 | 0.945 | – | – |

Reading: trained on eyecite's own labels the model reproduces eyecite's
recall ceiling (it recovers 879 of eyecite's 956 correct spans and only 10 of
the 111 eyecite missed) but it does **not** reproduce eyecite's precision
failures — it learned to leave statutes/regulations/record cites untagged
(3% vs 100% of curator-removed spans), lifting precision 0.858 → 0.911. Net
F1 is a tie with the floor (0.870 vs 0.877), recall down 6 points, precision up
5. The 8% of eyecite-found spans it drops and the 91% of curator-added spans
it misses are the two things LLM-corrected labels (round 2) should change.
Exp 3 tiny-set reference: extraction F1 0.74 (large).

### Task B — coreference linking (B³ over gold mentions, `runs/link_large-caselaw/best/coref_test.json`)

| | B³ P | B³ R | B³ F1 | attach rate (non-first mentions in the right cluster) |
|---|---|---|---|---|
| silver val (eyecite grouping) | 0.959 | 0.957 | 0.958 | 0.947 |
| **silver model on gold_dev (29 cases, 1,067 mentions)** | 0.974 | 0.922 | **0.947** | 0.888 (482/543) |

Attach by mention kind on gold_dev: supra 46/46, name 103/105 (0.98), id
62/70 (0.89), reporter short forms 271/322 (0.84); by source: eyecite-found
0.887, curator-added 0.889 — the linker treats curator-added mentions as well
as eyecite's, i.e. it generalizes beyond the silver grouping. Exp 3 tiny-set
reference: B³ ~0.61; the eyecite grouping floor under B³ on this gold is not
computed (eyecite groups are not carried in `citations.jsonl`; `citation_seed.py`
reports eyecite pairwise coref F1 0.975 on the same 49 dev opinions under a
different, mention-set-conditioned metric).

Weights: `runs/extract_large-caselaw/best/` and `runs/link_large-caselaw/best/`
(from `silver_runs.tar.gz`, 2.9 GB; both gitignored). Metrics + logs extracted
alongside.

### Apples-to-apples with the LLM seeding runs (triage scorer, 49 dev clusters, 2026-09-11)

The pod's own eval (exact char spans, 29 clusters — 20 dev `revised_html`
files had been zeroed by a viewer bug when the bundle was built; regenerated
2026-09-11, `data/output/gold_dev_fresh.jsonl`) is not comparable to the LLM
rows. So the checkpoints were run locally end to end
(`finetune/predict_gold_dev.py`: extraction → predicted-mention features →
linker; MPS, ~10 min) and the predictions scored with
`experiments_09022026/triage/score_encoder.py`, which anchors each predicted
span into the annotator text and calls the same `score_states` (overlap-matched
mention P/R/F1, pairwise coref P/R/F1 vs the current annotator gold) that
produced every other row. All 2,644 predicted spans anchored.

| system | mention P | mention R | mention F1 | coref P | coref R | coref F1 |
|---|---|---|---|---|---|---|
| eyecite (as tagged in the annotator) | 0.872 | 0.842 | 0.857 | 0.996 | 0.955 | 0.975 |
| **silver encoder** (CaseLawModernBERT-large: extraction → linking, end to end, no gold mentions) | 0.967 | 0.866 | **0.914** | 0.970 | 0.898 | **0.933** |
| Kimi K2.5, prompt v5 | 0.883 | 0.834 | 0.858 | 0.985 | 0.983 | 0.984 |
| GPT-5.6 Luna, v5, medium effort | 0.955 | 0.888 | 0.920 | 0.991 | 0.990 | 0.990 |
| Sonnet 4.6, v5 | 0.967 | 0.900 | 0.932 | 0.992 | 0.993 | 0.992 |
| GPT-5.6 Luna, v8g + candidates, high effort | 0.975 | 0.968 | 0.972 | 0.987 | 0.994 | 0.991 |
| Opus 5, v5 | 0.991 | 0.995 | 0.993 | 0.988 | 0.997 | 0.993 |

The encoder is the only row that is not an *edit* of eyecite's output — it
re-extracts from raw text — so its precision gain over eyecite (0.872 → 0.967:
no statutes/records) is genuine, and its recall (0.866) is bounded by the
silver labels it learned from: it cannot know the name-only mentions eyecite
never produced. On this scorer it sits between Kimi and GPT-medium on mention
F1 and below every LLM on coref (the linker runs on its own extraction, and
the LLMs edit eyecite's grouping, which is already at 0.975). Full cross-model
table with specs, prices and caveats: `../experiments_09022026/model_comparison_dev.md`.

## 6. Round 2 — 10K GPT-seeded set, warm-start fine-tune

### 6.1 Sample (Rachel ran it 2026-09-11)

`sample_cl_20k_round2.py` (same Django-shell procedure as §1; exclusion list
`inputs/exclude_cluster_ids_round2.csv` = 20,613 ids: benchmark, triage
annotator clusters, round-1 sample and its exclusion pool). Same 135 cells; each
takes 60 *keyword* + 15 *control* clusters, keyword = a strong negative-treatment
term (overrule, abrogate, distinguish, decline to follow, … — the regex list from
`triage/sample_clreplica.py`) within 300 chars of an eyecite citation span.

Run record (seed 20260911): **10,000 clusters** (8,210 keyword / 1,790
control), 142,867 candidates scanned in 2 h 22 m; zero overlap with the
exclusion list. Court groups: scotus 919, fed appellate 2,385, fed district
2,426, state supreme 2,225, state appellate 2,045; length bins short 3,499 /
medium 3,792 / long 2,709; mean 33.5K chars (median 22.6K), 65 eyecite spans
per opinion. Strong-term coverage: distinguish 3,681, overrule 2,445,
do-not-follow 1,043, undermine 858, superseded 667, abrogate 537, disapprove
477, inapposite 462. 41 of 135 cells did not reach 60 keyword picks (old
federal/SCOTUS long-opinion cells); the top-up pool covered the total. Data:
`data/cl20k_r2/` (181 MB).

### 6.2 Annotator root + seeding harness

`make_r2_annotator.py` unpacks `data/cl20k_r2/blocks/` into a sampler-shaped
`data/r2_sample/` and runs `triage/make_annotator_data.py` on it →
**`data/annotator_r2/`** (10,000 clusters, 216,993 authority rows, 641 MB;
`ids.txt`; `grouping_overrides/` is where seeding edits land). The triage
harness reads another root through `CITSEED_ANNOT` / `CITSEED_OUT` (honoured by
`citation_seed.py`, `bedrock_batch.py`, `openai_batch.py`):

```bash
cd ../experiments_09022026/triage
export CITSEED_ANNOT=$PWD/../../experiments_09092026/data/annotator_r2
export CITSEED_OUT=$PWD/../../experiments_09092026/data/citation_seed_r2
export OPENAI_KEY=...                                   # conda env `citator` has it
python3 citation_seed.py prepare --seed-state --ids $(cat $CITSEED_ANNOT/ids.txt)   # -> $CITSEED_OUT/inputs/ (449 MB)
bash openai_batch.sh export --name r2_gpt --model gpt-5.6-luna --prompt prompts/triage_citation_fixer_v8g.md \
     --effort high --candidates --max-tokens 128000 --ids $(cat $CITSEED_ANNOT/ids.txt)   # -> r2_gpt_pNN.jsonl (≤190 MB each)
bash openai_batch.sh submit --name r2_gpt_p01   # … p04; then status / fetch --name <part>
python3 citation_seed.py apply --write --ids <fetched ids> --out-dir $CITSEED_OUT/r2_gpt
./run_annotator.sh $CITSEED_ANNOT 8126          # viewer over the 10K set on :8126 (optional)
```

`r2_check.sh` (this folder) is the idempotent tick used while the batch ran:
status → fetch completed parts → apply unapplied ids → log to
`data/citation_seed_r2/checker.log`.

### 6.3 GPT seeding — OpenAI Batch, run record (2026-09-11)

GPT-5.6 Luna, prompt v8g + `<candidates>` block, reasoning effort high,
`max_completion_tokens` 128,000, fetch post-filter on adds. Four parts under the
200 MB file limit, submitted 14:51–15:03 PDT; batch rates $0.10 / $0.60 per M.

| part | requests | ok | errors | in tokens | out tokens | cost | applied |
|---|---|---|---|---|---|---|---|
| p01 | 2,836 | 2,835 | 1 (8895033 length) | 51,964,733 | 30,943,897 | $23.76 | 2,835 clusters; 63,909 edits, 842 skipped, 551 adds post-filtered |
| p02 | 2,582 | 2,580 | 2 (8891100 length, 112202 content_filter) | 51,774,801 | 29,598,189 | $22.94 | 2,580; 63,223 edits, 869 skipped, 434 post-filtered |
| p03 | 3,354 | 3,353 | 1 (5958284 length) | 51,276,195 | 29,518,857 | $22.84 | 3,353; 40,174 edits, 787 skipped, 286 post-filtered |
| p04 | 1,228 | 1,226 | 2 (2723464, 6557048 length) | 23,388,202 | 13,439,498 | $10.40 | 1,226; 24,033 edits, 412 skipped, 260 post-filtered |

**Totals:** 10,000 → 9,994 seeded, 6 failed. 178.4M input + 103.5M output
tokens = **$79.94** (estimate was $75–80). Wall time 14:51 → 22:11 ≈ 7.3 h
(the last handful of requests in p01/p04 sat ~3 h). Applied 191,339 edits into
`data/annotator_r2/data/grouping_overrides/` (109,254 add · 58,080 remove ·
18,137 merge · 5,868 regroup); 2,910 skipped, mostly adds inside an existing
tag or with unanchorable text; 1,531 adds dropped by the post-filter. 9,333
clusters received ≥1 edit, 661 came back unchanged from eyecite. Every applied
cluster carries `llm_citation_pass`; `overrides/citation_changes.csv` has one
`llm` row per edit. Applies of ~2.5K clusters take 15–20 min.

Failed (no JSON object in the response): `8895033` `8891100` `5958284`
`2723464` `6557048` hit the 128K output ceiling (very long opinions); `112202`
was refused by OpenAI's content filter. **Dropped from the training set, not
retried** (Rachel, 2026-09-14); a retry would be the real-time path
(`openai_batch.sh run --redo --ids …`).

Two `citation_seed.py` fixes came out of this run: the `apply --write` guard
refused every cluster whose inputs were built with `prepare --seed-state` (it
now refuses only when an override file already exists, i.e. gold/human edits);
and a GPT `regroup`/`add` edit without a `to` group crashed the run — such edits
are now skipped and logged.

### 6.4 Labels → dataset → pod package (2026-09-14)

```bash
bash export_r2_revised_html.sh              # viewer's revised_citation_html in-process against annotator_r2
                                            #   -> data/annotator_r2/data/revised_html/{cid}.html (9,994), ids_seeded.txt, ids_failed.txt
uv run --no-project python build_dataset.py \
    --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \
    --gold-splits  ../experiments_09022026/triage/inputs/splits.csv \
    --llm-train-ids ../experiments_09022026/triage/data/citation_seed/train_seeded_ids.txt \
    --gpt-train-revised data/annotator_r2/data/revised_html --gpt-train-ids data/annotator_r2/ids_seeded.txt \
    --out data/output/citations_r2.jsonl --validate
uv run --no-project python mention_features.py --jsonl data/output/citations_r2.jsonl --out data/output/mention_features_r2.jsonl
R2=1 bash finetune/make_pod_bundle.sh       # -> r2_pod_bundle.tar.gz (2.9 GB: code + data + init/ silver checkpoints)
```

`data/output/citations_r2.jsonl` (420 MB, `stats_r2.json`; export 4.5 min,
build ~4 min, 744,956 mention features):

| split | clusters | opinion records | mentions | GPT-added (`manual`) | removed (`negatives`) |
|---|---|---|---|---|---|
| train_gpt | 9,770 | 11,519 | 680,723 | 98,939 (14.5%) | 56,696 |
| val_gpt | 214 | 247 | 14,811 | 1,873 | 1,238 |
| train_llm | 261 | 435 | 42,577 | — | — |
| gold_dev | 49 | 68 | 2,960 | | |
| gold_test | 50 | 72 | 3,885 | | |

10 seeded clusters had no case-citation mention in any opinion and dropped out;
310 empty opinion records dropped. gold_dev is the full 49 clusters (the
silver-run build had 29 because of the zeroed files).

Design: the GPT-seeded clusters are gold-format records; 2% of clusters (by
hash) are held out as `val_gpt` so checkpoint selection uses the same label
distribution as training rather than eyecite silver; the 261 Opus-corrected
triage train clusters ride along as `train_llm`; no silver blocks — the warm
start already carries the silver signal and the cold control trains on exactly
the same GPT data. Warm start = `--init` from the round-1 `best/` checkpoints
(`train_extraction.py --init <dir>`, `train_linking.py --init <model.pt>`), lr
1e-5, 2 epochs; cold = HF backbone, lr 3e-5 / 2e-5, 3 epochs. Both train on
`train_gpt,train_llm`, select on `val_gpt`, report gold_dev; run names get
`_warm` / `_cold`. Local 1-step smokes of both warm-start paths passed on MPS.

On the pod:

```bash
tar xzf r2_pod_bundle.tar.gz && cd r2_pod/finetune
MODE=warm bash run_runpod.sh                 # -> runs/*_warm, r2_warm_runs.tar.gz
MODE=cold bash run_runpod.sh                 # control -> runs/*_cold, r2_cold_runs.tar.gz
runpodctl send r2_warm_runs.tar.gz
# laptop, after receiving + extracting:
RUN_SUFFIX=_warm SILVER_RUNS=<extracted runs dir> uv run python finetune/predict_gold_dev.py --records data/output/gold_dev_fresh.jsonl
python3 ../experiments_09022026/triage/score_encoder.py --pred data/output/pred_gold_dev_warm.json --ids $(cat ../experiments_09022026/triage/data/citation_seed/dev_ids.txt)
```

## Files

| file | role |
|---|---|
| `sample_cl_20k.py`, `inputs/exclude_cluster_ids.csv` | round-1 CLReplica sampler (Django shell, in the container) + never-sample list |
| `sample_cl_20k_round2.py`, `inputs/exclude_cluster_ids_round2.csv` | round-2 sampler (negative-signal oversampling) + exclusion list |
| `build_dataset.py` | html_with_citations / revised_html → citations.jsonl (silver, gold, `train_llm`, `train_gpt`/`val_gpt`) |
| `mention_features.py` | per-mention features for the linker |
| `make_r2_annotator.py` | round-2 blocks → `data/annotator_r2/` viewer root for the seeding harness |
| `export_r2_revised_html.{py,sh}` | GPT-seeded overrides → `revised_html/` labels + `ids_seeded.txt` / `ids_failed.txt` |
| `r2_check.sh` | idempotent OpenAI-batch checker used during the round-2 seeding |
| `finetune/{common,train_extraction,train_linking,linking_model,metrics}.py` | harness (from Exp 3); `--init` = warm start, `CIT_JSONL`/`CIT_FEATS` = alternate build |
| `finetune/run_runpod.sh`, `finetune/make_pod_bundle.sh` | pod runner (`MODE=silver\|warm\|cold`) / packer (`R2=1` for round 2) |
| `finetune/predict_gold_dev.py` | local end-to-end inference over gold_dev for the triage scorer (`RUN_SUFFIX`) |

Gitignored: `data/`, `runs/`, `*.tar.gz`, `*.log`, `finetune/.venv/`.
