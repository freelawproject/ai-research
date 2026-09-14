#!/usr/bin/env bash
# Self-contained pod bundle: finetune code + the built dataset.
#
#   bash finetune/make_pod_bundle.sh          # silver: silver_pod_bundle.tar.gz (no weights; pod pulls the HF backbone)
#   R2=1 bash finetune/make_pod_bundle.sh     # round 2: r2_pod_bundle.tar.gz = code + citations_r2/mention_features_r2
#                                             #   (staged under the canonical names) + init/ = the silver checkpoints
#                                             #   (~3 GB) for MODE=warm; MODE=cold uses the same bundle without them
set -euo pipefail
cd "$(dirname "$0")"
FT="$PWD"; EXP="$(dirname "$FT")"
if [ "${R2:-0}" = 1 ]; then
  TAG=r2; SFX=_r2; OUT="$EXP/r2_pod_bundle.tar.gz"
else
  TAG=silver; SFX=; OUT="$EXP/silver_pod_bundle.tar.gz"
fi
for f in "citations$SFX.jsonl" "mention_features$SFX.jsonl"; do
  [ -f "$EXP/data/output/$f" ] || { echo "ERROR: missing data/output/$f — run build_dataset.py + mention_features.py" >&2; exit 1; }
done
STAGE="$(mktemp -d)/${TAG}_pod"
mkdir -p "$STAGE/finetune" "$STAGE/data/output"
for f in *.py *.sh *.toml; do [ -e "$f" ] && cp "$f" "$STAGE/finetune/"; done
# the trainers read the canonical names (common.py: ../data/output/citations.jsonl)
cp "$EXP/data/output/citations$SFX.jsonl" "$STAGE/data/output/citations.jsonl"
cp "$EXP/data/output/mention_features$SFX.jsonl" "$STAGE/data/output/mention_features.jsonl"
if [ "$TAG" = r2 ]; then
  for r in extract_large-caselaw link_large-caselaw; do
    [ -d "$EXP/runs/$r/best" ] || { echo "ERROR: missing runs/$r/best (silver checkpoints for the warm start)" >&2; exit 1; }
    mkdir -p "$STAGE/init/$r"
    cp -R "$EXP/runs/$r/best" "$STAGE/init/$r/best"
  done
  rm -f "$STAGE"/init/*/best/coref_*.json "$STAGE"/init/*/best/training_args.bin
  cat > "$STAGE/RUN.md" <<'MD'
# round 2 — GPT-seeded citation extraction + coref, warm vs cold start — pod bundle

    finetune/            code (common.py resolves ../data/output/*)
    data/output/         citations.jsonl: train_gpt / val_gpt (9,994 GPT-seeded clusters, 2% val by hash)
                         + train_llm (261 Opus-corrected triage train clusters) + gold_dev / gold_test
    init/                silver checkpoints (round 1) = the warm-start weights

    cd finetune
    MODE=warm bash run_runpod.sh          # init from ../init, lr 1e-5, 2 epochs -> runs/*_warm, r2_warm_runs.tar.gz
    MODE=cold bash run_runpod.sh          # HF backbone, lr 3e-5/2e-5, 3 epochs -> runs/*_cold, r2_cold_runs.tar.gz
    MAX_TRAIN=2000 MODE=warm bash run_runpod.sh   # pilot
    runpodctl send r2_warm_runs.tar.gz    # back on the laptop: runpodctl receive <code>; then
                                          # SILVER_RUNS=<extracted runs dir> predict_gold_dev.py -> score_encoder.py

Selection is on val_gpt (same label distribution as training); eval_test.json /
coref_test.json report gold_dev with the exact-span metric — the headline
(triage-scorer) numbers come from predict_gold_dev.py + score_encoder.py on the
laptop. Both modes can run on one 80GB card back to back (MODE=warm; MODE=cold).
The HF model is public; if the download needs a token: `export HF_TOKEN=...`.
MD
else
  cat > "$STAGE/RUN.md" <<'MD'
# eyecite-seeded citation extraction + coref — pod bundle

    finetune/            code (common.py resolves ../data/output/*)
    data/output/         citations.jsonl (train/val silver + gold_dev/gold_test) + mention_features.jsonl

    cd finetune
    bash run_runpod.sh                    # CaseLawModernBERT-large, extraction then linking
    MAX_TRAIN=2000 bash run_runpod.sh     # pilot on 2,000 clusters first
    runpodctl send silver_runs.tar.gz     # results: runs/*/eval_test.json = gold_dev scores

The HF model is public; if the download needs a token: `export HF_TOKEN=...`.
MD
fi
rm -f "$OUT"; tar czf "$OUT" -C "$(dirname "$STAGE")" "${TAG}_pod"; rm -rf "$(dirname "$STAGE")"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "pod: tar xzf $(basename "$OUT") && cd ${TAG}_pod/finetune && ${SFX:+MODE=warm }bash run_runpod.sh"
