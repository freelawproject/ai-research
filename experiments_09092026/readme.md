# 0909 — Citation extraction + coreference encoder on CaseLawModernBERT-large

`experiments_09022026` (the citator quality review) turned up eyecite's
extraction/coreference error rate as a real problem, so I wanted a trainable
encoder that does both tasks (Task A: BIO extraction over case-citation
spans; Task B: antecedent-ranking coreference) directly on opinion text —
something that can eventually run instead of, or alongside, eyecite. Measured
throughout against the 49-cluster human-verified dev set from that experiment.

## What I tried

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
OpenAI Batch API: **9,994/10,000 seeded successfully for $79.94** (178M input
+ 104M output tokens, ~7.3h wall time across 4 batch parts), applying 191,339
corrections (adds/removes/merges/regroups) to eyecite's grouping. Six
clusters failed — five hit the 128K output ceiling on very long opinions, one
was refused by OpenAI's content filter — and were **dropped rather than
retried** (2026-09-14).

Built into a labeled dataset (`citations_r2.jsonl`, 420 MB):

| split | clusters | mentions | GPT-added | GPT-removed |
|---|---|---|---|---|
| train_gpt | 9,770 | 680,723 | 98,939 (14.5%) | 56,696 |
| val_gpt | 214 | 14,811 | 1,873 | 1,238 |
| train_llm (Opus-corrected, from 0902) | 261 | 42,577 | — | — |
| gold_dev / gold_test | 49 / 50 | 2,960 / 3,885 | – | – |

2% of GPT-seeded clusters (by hash) are held out as `val_gpt` so checkpoint
selection uses the same label distribution as training, rather than eyecite
silver. Packaged into a 2.9 GB pod bundle that includes the round-1 silver
checkpoints, for a warm-start fine-tune (`--init` from those checkpoints, 2
epochs) against a cold-start control (fresh backbone, 3 epochs) — both train
on `train_gpt,train_llm`, select on `val_gpt`, report on gold_dev.

## Current status

Package built and verified (both warm-start trainers smoke-tested on MPS).
**Pod run pending** — Rachel runs `MODE=warm` then `MODE=cold` herself; not
started as of 2026-09-14.

## Next steps

1. Run warm + cold on the pod, fetch both result sets.
2. Score both against gold_dev with the same triage scorer used above; compare
   to silver (0.914/0.933) and to GPT v8g (0.972/0.991) — does warm-starting
   from silver beat cold, and does either approach the LLM ceiling?
3. Decide whether to retry the 6 failed round-2 clusters.
4. gold_test stays untouched until there's a checkpoint worth reporting a
   final number for.

## How to reproduce

```bash
# 1. Sample (Rachel runs this — requires the CourtListener docker stack)
docker cp inputs/exclude_cluster_ids_round2.csv cl-django:/opt/courtlistener/
docker cp sample_cl_20k_round2.py cl-django:/opt/courtlistener/
docker exec cl-django python manage.py shell -c "exec(open('sample_cl_20k_round2.py').read())"
docker cp cl-django:/opt/courtlistener/cl20k_r2/ ./data/

# 2. Annotator root + GPT seeding harness (see experiments_09022026/triage/docs/checklist.md
#    for the harness itself; this is the round-2-specific wiring)
python3 make_r2_annotator.py                # data/cl20k_r2/ -> data/annotator_r2/
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
bash export_r2_revised_html.sh
uv run --no-project python build_dataset.py \
    --gold-revised ../experiments_09022026/triage/data/annotator/data/revised_html \
    --gold-splits  ../experiments_09022026/triage/inputs/splits.csv \
    --llm-train-ids ../experiments_09022026/triage/data/citation_seed/train_seeded_ids.txt \
    --gpt-train-revised data/annotator_r2/data/revised_html --gpt-train-ids data/annotator_r2/ids_seeded.txt \
    --out data/output/citations_r2.jsonl --validate
uv run --no-project python mention_features.py --jsonl data/output/citations_r2.jsonl --out data/output/mention_features_r2.jsonl
R2=1 bash finetune/make_pod_bundle.sh        # -> r2_pod_bundle.tar.gz (2.9 GB, incl. round-1 checkpoints)

# 4. On the pod
tar xzf r2_pod_bundle.tar.gz && cd r2_pod/finetune
MODE=warm bash run_runpod.sh                 # -> runs/*_warm, r2_warm_runs.tar.gz
MODE=cold bash run_runpod.sh                 # control -> runs/*_cold, r2_cold_runs.tar.gz

# 5. Back on the laptop, score against the triage scorer
RUN_SUFFIX=_warm SILVER_RUNS=<extracted runs dir> uv run python finetune/predict_gold_dev.py --records data/output/gold_dev_fresh.jsonl
python3 ../experiments_09022026/triage/analysis/score_encoder.py --pred data/output/pred_gold_dev_warm.json \
    --ids $(cat ../experiments_09022026/triage/data/citation_seed/dev_ids.txt)
```

`run_runpod.sh` picks batch size / gradient accumulation from available VRAM
automatically (`BATCH=auto`, keeps effective batch at 16); override with
`BATCH=<n> ACCUM=<n>` by hand. Requires `transformers<5` (pinned in
`finetune/pyproject.toml`; both trainers also work on 5.x via
`common.warmup_kwargs()`).

## Layout

```
sample_cl_20k.py, sample_cl_20k_round2.py     CLReplica samplers (round 1 / round 2)
inputs/exclude_cluster_ids*.csv               never-sample lists (tracked)
build_dataset.py, mention_features.py         html_with_citations / revised_html -> labeled dataset
make_r2_annotator.py, export_r2_revised_html.{py,sh}, r2_check.sh   round-2 seeding-harness wiring
finetune/       training harness (ported from Exp 3): common, train_extraction, train_linking,
                linking_model, metrics, predict_gold_dev, run_runpod.sh, make_pod_bundle.sh
data/, runs/    gitignored: sampled blocks, built datasets, trained weights, pod tarballs, logs
```
