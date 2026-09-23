#!/usr/bin/env bash
# Pod-side runner for the INFERENCE kit (make_predict_bundle.sh): the production
# encoder (extract_large-caselaw_warm + link_large-caselaw_r4 + Id. rule) over
# the round-2 training records, so the encoder's labels can be diffed against
# the eyecite+GPT labels it was trained on (disagreement_select.py, laptop side).
#
#   bash run_predict_pod.sh                      # train,validation -> ../data/output/pred_train_validation_r4_rule.json
#   SPLIT=validation bash run_predict_pod.sh        # smaller split first (247 records, minutes)
#   LIMIT=50 bash run_predict_pod.sh             # smoke: first 50 records
#   BATCH=8 bash run_predict_pod.sh              # extraction windows per forward (default 16; 4096 tokens each)
set -euo pipefail
cd "$(dirname "$0")"
pip install -q "transformers>=4.48,<5" "torch>=2.4" numpy tqdm

SPLIT=${SPLIT:-train,validation}
NAME=${NAME:-pred_${SPLIT//,/_}_r4_rule}
OUT=../data/output/${NAME}.json
export RUN_SUFFIX=_warm LINK_SUFFIX=_r4      # runs/extract_large-caselaw_warm + runs/link_large-caselaw_r4
echo "split=$SPLIT batch=${BATCH:-16} limit=${LIMIT:-none} -> $OUT"
python inference.py --split "$SPLIT" --records ../data/output/citations.jsonl --out "$OUT" \
    --batch "${BATCH:-16}" --max-mentions 0 ${LIMIT:+--limit $LIMIT} 2>&1 | tee "${NAME}.log"
tar czf "${NAME}.tar.gz" -C .. "data/output/${NAME}.json" -C model "${NAME}.log"
echo "DONE — fetch with: runpodctl send ${NAME}.tar.gz"
