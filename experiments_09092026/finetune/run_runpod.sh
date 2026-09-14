#!/usr/bin/env bash
# Pod-side runner: Task A (extraction) then Task B (coref linking).
#
#   bash run_runpod.sh                     # MODE=silver (default): eyecite-seeded silver set, select on val
#   MODE=warm bash run_runpod.sh           # round 2: GPT-seeded train_gpt+train_llm, select on val_gpt,
#                                          #   init from ../init/*/best (silver checkpoints), lr 1e-5, 2 epochs
#   MODE=cold bash run_runpod.sh           # round 2 control: same data, from the HF backbone, default lr/epochs
#   bash run_runpod.sh large-caselaw A     # extraction only (B = linking only)
#   MAX_TRAIN=2000 bash run_runpod.sh      # cap training clusters (pilot)
#   FLASH=1 bash run_runpod.sh             # build flash-attn (optional; sdpa default)
#   bash run_runpod.sh                     # BATCH=auto: size extraction batch from VRAM (effective 16)
#   BATCH=8 ACCUM=2 CKPT=0 bash run_runpod.sh   # manual override (ACCUM defaults to 16/BATCH)
# Any MODE default can be overridden by env: TRAIN_SPLIT VAL_SPLIT EPOCHS LR_A LR_B INIT_A INIT_B SUFFIX.
set -euo pipefail
cd "$(dirname "$0")"

# transformers pinned to 4.x: 5.x removed TrainingArguments.warmup_ratio and
# changed loading/Trainer internals; 4.48+ is what the local smoke was validated on.
pip install -q "transformers>=4.48,<5" "torch>=2.4" seqeval accelerate numpy tqdm
if [ "${FLASH:-0}" = 1 ]; then
  pip install -q flash-attn --no-build-isolation || echo "flash-attn build failed (sdpa fallback)"
fi

BASE=${1:-large-caselaw}
TASKS=${2:-AB}
MODE=${MODE:-silver}
case "$MODE" in
  silver)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train}; VAL_SPLIT=${VAL_SPLIT:-val}
    EPOCHS=${EPOCHS:-3}; LR_A=${LR_A:-3e-5}; LR_B=${LR_B:-2e-5}
    INIT_A=${INIT_A:-}; INIT_B=${INIT_B:-}; SUFFIX=${SUFFIX:-}; RESULTS=silver_runs.tar.gz ;;
  warm)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train_gpt,train_llm}; VAL_SPLIT=${VAL_SPLIT:-val_gpt}
    EPOCHS=${EPOCHS:-2}; LR_A=${LR_A:-1e-5}; LR_B=${LR_B:-1e-5}
    INIT_A=${INIT_A:-../init/extract_large-caselaw/best}
    INIT_B=${INIT_B:-../init/link_large-caselaw/best/model.pt}
    SUFFIX=${SUFFIX:-_warm}; RESULTS=r2_warm_runs.tar.gz ;;
  cold)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train_gpt,train_llm}; VAL_SPLIT=${VAL_SPLIT:-val_gpt}
    EPOCHS=${EPOCHS:-3}; LR_A=${LR_A:-3e-5}; LR_B=${LR_B:-2e-5}
    INIT_A=${INIT_A:-}; INIT_B=${INIT_B:-}; SUFFIX=${SUFFIX:-_cold}; RESULTS=r2_cold_runs.tar.gz ;;
  *) echo "unknown MODE=$MODE (silver|warm|cold)" >&2; exit 2 ;;
esac
for p in $INIT_A $INIT_B; do
  [ -e "$p" ] || { echo "ERROR: warm-start init $p not found (bundle built without R2=1?)" >&2; exit 1; }
done
NAME_A="extract_${BASE//\//_}${SUFFIX}"; NAME_B="link_${BASE//\//_}${SUFFIX}"
CAP=${MAX_TRAIN:+--max-train-docs $MAX_TRAIN}
GPUS=$(nvidia-smi -L 2>/dev/null | wc -l || echo 1)
echo "mode=$MODE base=$BASE tasks=$TASKS gpus=$GPUS train=$TRAIN_SPLIT val=$VAL_SPLIT epochs=$EPOCHS" \
     "lr_a=$LR_A lr_b=$LR_B init_a=${INIT_A:-none} init_b=${INIT_B:-none} ${MAX_TRAIN:+max_train=$MAX_TRAIN}"

fails=""
if [[ "$TASKS" == *A* ]]; then
  echo "=== extraction $NAME_A ==="
  # ModernBERT-large @ 4096 tokens. Effective batch = BATCH*ACCUM (keep 16 so the
  # LR schedule is unchanged). Defaults are conservative (~20% of an 80GB card);
  # on 80GB use BATCH=8 ACCUM=2 CKPT=0 (no checkpointing = ~30% faster), or
  # BATCH=16 ACCUM=1 with CKPT=1. On 48GB: BATCH=4 ACCUM=4 CKPT=1.
  # BATCH=auto (default) picks (batch, accum, checkpointing) from total VRAM,
  # always keeping batch*accum = 16. Override any of BATCH/ACCUM/CKPT by hand.
  if [ "${BATCH:-auto}" = auto ]; then
    read -r BATCH ACCUM CKPT <<<"$(python - <<'PY'
import torch
gb = torch.cuda.get_device_properties(0).total_memory / 2**30 if torch.cuda.is_available() else 0
# ModernBERT-large @ 4096 tokens, bf16, AdamW. Measured: batch 4 + ckpt on = ~10.5GB
# (states ~9GB, checkpointed activations <1GB); ckpt off costs ~2-3GB per sample.
for lim, cfg in ((75, "8 2 0"), (40, "4 4 0"), (22, "2 8 1")):
    if gb >= lim:
        print(cfg); break
else:
    print("1 16 1")
print(f"[auto-batch] {gb:.0f}GB VRAM", file=__import__('sys').stderr)
PY
)"
  else
    BATCH=${BATCH}; ACCUM=${ACCUM:-$((16 / BATCH))}; CKPT=${CKPT:-1}
  fi
  echo "extraction: batch=$BATCH accum=$ACCUM ckpt=$CKPT (effective $((BATCH*ACCUM)))"
  python train_extraction.py --base "$BASE" --device cuda $CAP --name "$NAME_A" \
      --train-split "$TRAIN_SPLIT" --val-split "$VAL_SPLIT" --epochs "$EPOCHS" --lr "$LR_A" \
      ${INIT_A:+--init "$INIT_A"} \
      --batch "$BATCH" --grad-accum "$ACCUM" --max-length 4096 \
      ${CKPT:+$([ "$CKPT" = 1 ] && echo --grad-checkpoint)} --eval-steps 1000 \
      2>&1 | tee "${NAME_A}.log" || fails="$fails extract"
fi
if [[ "$TASKS" == *B* ]]; then
  echo "=== linking $NAME_B ==="
  python train_linking.py --base "$BASE" --device cuda $CAP --name "$NAME_B" \
      --train-split "$TRAIN_SPLIT" --val-split "$VAL_SPLIT" --epochs "$EPOCHS" --lr "$LR_B" \
      ${INIT_B:+--init "$INIT_B"} \
      --lwin 64 --ctx 128 --grad-checkpoint \
      2>&1 | tee "${NAME_B}.log" || fails="$fails link"
fi
[ -n "$fails" ] && echo "FAILED:$fails" || echo "all runs OK"

tar czf "$RESULTS" runs/*/best runs/*/eval_val.json runs/*/eval_test.json \
  runs/*/best/coref_val.json runs/*/best/coref_test.json *.log 2>/dev/null || tar czf "$RESULTS" runs *.log
echo "DONE — fetch with: runpodctl send $RESULTS"
