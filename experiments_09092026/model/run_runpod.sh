#!/usr/bin/env bash
# Pod-side runner: Task A (extraction) then Task B (coref linking).
#
#   bash run_runpod.sh                     # MODE=silver (default): eyecite-seeded silver set, select on val
#   MODE=warm bash run_runpod.sh           # round 2: GPT-seeded train+train_high_quality, select on validation,
#                                          #   init from ../init/*/best (silver checkpoints), lr 1e-5, 2 epochs
#   MODE=cold bash run_runpod.sh           # round 2 control: same data, from the HF backbone, default lr/epochs
#   MODE=r3link bash run_runpod.sh         # round 3: Task B only, injected no-antecedent Id./supra (negative result)
#   MODE=r4link bash run_runpod.sh         # round 4: Task B only, lwin 64->128 with chunk recompute (the window test)
#   MODE=r5link bash run_runpod.sh         # round 5: Task B only, warm from the r4 linker, + eyecite's grouping as an
#                                          #   input channel (3 pair features, 20% case-level dropout); EYE=0 = control
#                                          #   (same warm start + data + 2 more epochs, no channel) -> runs/*_r5ctl
#   bash run_runpod.sh large-caselaw A     # extraction only (B = linking only)
#   MAX_TRAIN=2000 bash run_runpod.sh      # cap training clusters (pilot)
#   FLASH=1 bash run_runpod.sh             # build flash-attn (optional; sdpa default)
#   bash run_runpod.sh                     # BATCH=auto: size extraction batch from VRAM (effective 16)
#   BATCH=8 ACCUM=2 CKPT=0 bash run_runpod.sh   # manual override (ACCUM defaults to 16/BATCH)
# Any MODE default can be overridden by env: TRAIN_SPLIT VAL_SPLIT EPOCHS LR_A LR_B INIT_A INIT_B SUFFIX
# LWIN CTX CHUNK LINK_FLAGS (CHUNK = linker slice budget in tokens; lower it if the card is tight).
set -euo pipefail
cd "$(dirname "$0")"

# transformers pinned to 4.x: 5.x removed TrainingArguments.warmup_ratio and
# changed loading/Trainer internals; 4.48+ is what the local smoke was validated on.
pip install -q "transformers>=4.48,<5" "torch>=2.4" seqeval accelerate numpy tqdm
if [ "${FLASH:-0}" = 1 ]; then
  pip install -q flash-attn --no-build-isolation || echo "flash-attn build failed (sdpa fallback)"
fi

export PYTORCH_CUDA_ALLOC_CONF=${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}
BASE=${1:-large-caselaw}
TASKS=${2:-AB}
MODE=${MODE:-silver}
case "$MODE" in
  silver)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train}; VAL_SPLIT=${VAL_SPLIT:-val}
    EPOCHS=${EPOCHS:-3}; LR_A=${LR_A:-3e-5}; LR_B=${LR_B:-2e-5}
    INIT_A=${INIT_A:-}; INIT_B=${INIT_B:-}; SUFFIX=${SUFFIX:-}; RESULTS=silver_runs.tar.gz ;;
  warm)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train,train_high_quality}; VAL_SPLIT=${VAL_SPLIT:-validation}
    EPOCHS=${EPOCHS:-2}; LR_A=${LR_A:-1e-5}; LR_B=${LR_B:-1e-5}
    INIT_A=${INIT_A:-../init/extract_large-caselaw/best}
    INIT_B=${INIT_B:-../init/link_large-caselaw/best/model.pt}
    SUFFIX=${SUFFIX:-_warm}; RESULTS=r2_warm_runs.tar.gz ;;
  cold)
    TRAIN_SPLIT=${TRAIN_SPLIT:-train,train_high_quality}; VAL_SPLIT=${VAL_SPLIT:-validation}
    EPOCHS=${EPOCHS:-3}; LR_A=${LR_A:-3e-5}; LR_B=${LR_B:-2e-5}
    INIT_A=${INIT_A:-}; INIT_B=${INIT_B:-}; SUFFIX=${SUFFIX:-_cold}; RESULTS=r2_cold_runs.tar.gz ;;
  r3link)
    # round 3: linking only, same data + same warm start + same window as
    # MODE=warm, so the ONLY difference vs link_large-caselaw_warm is the
    # injected no-antecedent mentions. Widening the window is a separate
    # experiment: at lwin=256 the retained activations are 4x and it OOMs,
    # because chunking bounds the peak per call but training still holds every
    # chunk until backward.
    TASKS=B
    TRAIN_SPLIT=${TRAIN_SPLIT:-train,train_high_quality}; VAL_SPLIT=${VAL_SPLIT:-validation}
    EPOCHS=${EPOCHS:-2}; LR_A=${LR_A:-1e-5}; LR_B=${LR_B:-1e-5}
    INIT_A=${INIT_A:-}
    INIT_B=${INIT_B:-../init/link_large-caselaw/best/model.pt}
    LWIN=${LWIN:-64}; CTX=${CTX:-128}    # round-2 values: dummies are the only variable
    LINK_FLAGS=${LINK_FLAGS:---grad-checkpoint --dummies}
    SUFFIX=${SUFFIX:-_r3}; RESULTS=r3_link_runs.tar.gz ;;
  r4link)
    # round 4: linking only, the WINDOW is the variable. Same data, warm start
    # and 700-mention cap as MODE=warm; no injected dummies (round 3 showed they
    # can't be told from real Id.s at this window and pushed id attach DOWN,
    # 0.894 -> 0.808). lwin 64 -> 128 because 79.6% of round-2 windows hit the
    # 64-subword cap, i.e. the model never saw the ctx=128 it was fetching.
    # --chunk-checkpoint recomputes each slice in backward, so the wider window
    # cannot OOM the way lwin=256 did in round 3 (38.8 GiB retained).
    TASKS=B
    TRAIN_SPLIT=${TRAIN_SPLIT:-train,train_high_quality}; VAL_SPLIT=${VAL_SPLIT:-validation}
    EPOCHS=${EPOCHS:-2}; LR_A=${LR_A:-1e-5}; LR_B=${LR_B:-1e-5}
    INIT_A=${INIT_A:-}
    INIT_B=${INIT_B:-../init/link_large-caselaw/best/model.pt}
    LWIN=${LWIN:-128}; CTX=${CTX:-128}
    LINK_FLAGS=${LINK_FLAGS:---chunk-checkpoint}
    SUFFIX=${SUFFIX:-_r4}; RESULTS=r4_link_runs.tar.gz ;;
  r5link)
    # round 5: linking only, the eyecite CHANNEL is the variable ("edit-formulated"
    # linker: it starts from eyecite's groups for the spans eyecite found instead
    # of from scratch). Warm start = the round-4 linker (its pair scorer is padded
    # with zero columns for the 3 new features, so step 0 == r4), same data,
    # window (lwin 128 / ctx 128), cap and chunk recompute as MODE=r4link. The
    # channel is masked on EYE_DROPOUT of training cases so the model also works
    # without eyecite; coref_test_noeye.json reports that condition. EYE=0 trains
    # the identical control (r4 + 2 more epochs, no channel) so the delta is the
    # channel and not the extra training.
    TASKS=B
    TRAIN_SPLIT=${TRAIN_SPLIT:-train,train_high_quality}; VAL_SPLIT=${VAL_SPLIT:-validation}
    EPOCHS=${EPOCHS:-2}; LR_A=${LR_A:-1e-5}; LR_B=${LR_B:-1e-5}
    INIT_A=${INIT_A:-}
    INIT_B=${INIT_B:-../init/link_large-caselaw_r4/best/model.pt}
    LWIN=${LWIN:-128}; CTX=${CTX:-128}
    if [ "${EYE:-1}" = 1 ]; then
      LINK_FLAGS=${LINK_FLAGS:---chunk-checkpoint --eyecite-feats --eyecite-dropout ${EYE_DROPOUT:-0.2}}
      SUFFIX=${SUFFIX:-_r5}
    else
      LINK_FLAGS=${LINK_FLAGS:---chunk-checkpoint}
      SUFFIX=${SUFFIX:-_r5ctl}
    fi
    RESULTS=r5_link_runs${SUFFIX}.tar.gz ;;
  *) echo "unknown MODE=$MODE (silver|warm|cold|r3link|r4link|r5link)" >&2; exit 2 ;;
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
      --lwin "${LWIN:-64}" --ctx "${CTX:-128}" --chunk "${CHUNK:-32768}" ${LINK_FLAGS:---grad-checkpoint} \
      2>&1 | tee "${NAME_B}.log" || fails="$fails link"
fi
[ -n "$fails" ] && echo "FAILED:$fails" || echo "all runs OK"

tar czf "$RESULTS" runs/*/best runs/*/eval_val.json runs/*/eval_test.json \
  runs/*/best/coref_val.json runs/*/best/coref_test.json *.log 2>/dev/null || tar czf "$RESULTS" runs *.log
echo "DONE — fetch with: runpodctl send $RESULTS"
