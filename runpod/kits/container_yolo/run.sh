#!/usr/bin/env bash
# Container-YOLO layout inference — POD-SIDE runner.
# Run from the extracted kit root (holds infer.py, images/).
#
#   runpodctl receive <code>
#   tar xzf container_yolo_<set>_kit.tar.gz && cd container_yolo
#   bash run.sh                       # installs deps, infers, tars out/
#   runpodctl send container_yolo_out_<set>.tar.gz
#
# Multi-GPU: `GPUS=n bash run.sh` runs one worker per card (shards 0/n … n-1/n)
# and packages out/ once at the end. GPUS defaults to every GPU on the pod.
# CPU works too, just slowly — this is the one kit that does not need a GPU.
#
# The checkpoint is pulled from the Hub (freelawproject/container-yolo, public,
# no token). Set WEIGHTS=/path/to.pt to run a local one instead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
SHARD="${SHARD:-0/1}"
SET="$(cat SET 2>/dev/null || echo out)"
OUT_DIR=out
TAR="container_yolo_out_${SET}.tar.gz"
# One worker per GPU (no-op on a single card / CPU); never returns in the parent.
source "$ROOT/fanout.sh"
gpu_fanout yolo

# Count what exists, and ONLY what exists: a missing directory makes `find` exit
# 1, `pipefail` carries that status out of the pipeline, and `set -e` then kills
# the script — with find's stderr discarded, silently and before any output. On a
# fresh extract images/ does not exist yet, so this is the normal path.
NIMG=0; [[ -d images ]] && NIMG=$(find images -maxdepth 1 -name '*.png' -not -name '._*' | wc -l | tr -d ' ')
[[ "$NIMG" -gt 0 ]] || {
  echo "!! no page images staged — images/ is built by pack.sh, so this kit" >&2
  echo "!! was packed wrong or unpacked incompletely" >&2
  exit 1; }

echo ">> [yolo] set=$SET, shard=$SHARD ($NIMG page images)"

if [[ "${SKIP_DEPS:-0}" == 1 ]]; then
  echo ">> [yolo] SKIP_DEPS=1 — another worker installed the deps"
else
  echo ">> [yolo] installing deps"
  pip install -q doclayout-yolo huggingface_hub dill pillow torch
  deps_ready
fi

echo ">> [yolo] $NIMG page images ready"

echo ">> [yolo] inferring (shard $SHARD)"
# Weights come from the Hub (freelawproject/container-yolo), cached after the
# first pull. Override with WEIGHTS=/path/to.pt for an unpublished checkpoint.
python infer.py --images-dir images --out-dir out \
  --shard "$SHARD" ${WEIGHTS:+--weights "$WEIGHTS"}

# A fan-out child leaves packaging to the parent, which tars the shared out/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out yolo
