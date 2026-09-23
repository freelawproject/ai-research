#!/usr/bin/env bash
# Inference kit: the production encoder checkpoints + the round-2 records + the
# code to run inference.py over them on a pod (run_predict_pod.sh).
#
#   bash model/make_predict_bundle.sh      # -> r4_predict_bundle.tar.gz (~3.5 GB: two 1.5 GB checkpoints + 400 MB records)
#
# Purpose (2026-09-18): diff the encoder's labels against the eyecite+GPT labels
# of the round-2 training set (experiments_09022026/triage/analysis/disagreement_select.py)
# to count the disagreements before deciding on an Opus adjudication pass.
set -euo pipefail
cd "$(dirname "$0")"
FT="$PWD"; EXP="$(dirname "$FT")"
OUT="$EXP/r4_predict_bundle.tar.gz"
for f in "$EXP/data/output/citations_r2.jsonl" "$EXP/runs/extract_large-caselaw_warm/best/model.safetensors" \
         "$EXP/runs/link_large-caselaw_r4/best/model.pt"; do
  [ -f "$f" ] || { echo "ERROR: missing $f" >&2; exit 1; }
done
STAGE="$(mktemp -d)/r4_predict"
mkdir -p "$STAGE/model" "$STAGE/data/output" "$STAGE/runs/extract_large-caselaw_warm" "$STAGE/runs/link_large-caselaw_r4/best"
for f in *.py *.sh *.toml; do [ -e "$f" ] && cp "$f" "$STAGE/model/"; done
cp "$EXP/corpus/mention_features.py" "$EXP/corpus/build_dataset.py" "$STAGE/"   # inference imports mention_features (-> build_dataset)
cp "$EXP/data/output/citations_r2.jsonl" "$STAGE/data/output/citations.jsonl"
cp -R "$EXP/runs/extract_large-caselaw_warm/best" "$STAGE/runs/extract_large-caselaw_warm/best"
rm -f "$STAGE/runs/extract_large-caselaw_warm/best/training_args.bin"
cp "$EXP/runs/link_large-caselaw_r4/best/model.pt" "$STAGE/runs/link_large-caselaw_r4/best/model.pt"
cat > "$STAGE/RUN.md" <<'MD'
# production encoder over the round-2 training records — inference kit

    model/               code; inference.py reads ../runs/<checkpoints> and ../data/output/citations.jsonl
    data/output/         citations.jsonl = the round-2 records (train 11,519 / validation 247 records + the gold splits)
    runs/                extract_large-caselaw_warm (extraction) + link_large-caselaw_r4 (linker); the Id. rule is in the code

    cd model
    LIMIT=50 bash run_predict_pod.sh            # smoke (a minute)
    SPLIT=validation bash run_predict_pod.sh       # 247 records, minutes -> pred_validation_r4_rule.json
    bash run_predict_pod.sh                     # train,validation -> pred_train_validation_r4_rule.json (+ .log, .tar.gz)
    runpodctl send pred_train_validation_r4_rule.tar.gz

No gold is touched (gold_dev / gold_test records are in the file but not selected).
Extraction batch defaults to 16 windows of 4096 tokens (fine on 48-80 GB); linking
runs chunked under no_grad with no mention cap. Laptop reference: 68 dev records
took ~3 min on an M4 Pro, so ~11.8K records is ~8 h there and well under an hour here.

Back on the laptop (experiments_09022026/triage):
    export CITSEED_ANNOT=…/experiments_09092026/data/annotator_r2 CITSEED_OUT=…/experiments_09092026/data/citation_seed_r2
    python3 analysis/disagreement_select.py --pred …/experiments_09092026/data/output/pred_train_validation_r4_rule.json
    # -> inputs_adjudicate/stats.json + summary.csv = how many disagreements, of what kind, in how many opinions
MD
rm -f "$OUT"; tar czf "$OUT" -C "$(dirname "$STAGE")" r4_predict; rm -rf "$(dirname "$STAGE")"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo "pod: tar xzf $(basename "$OUT") && cd r4_predict/model && LIMIT=50 bash run_predict_pod.sh"
