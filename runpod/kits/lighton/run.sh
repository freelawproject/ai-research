#!/usr/bin/env bash
# LightOn batched transcription — POD-SIDE runner. The pod does ONLY inference:
# crops + manifest are built locally from the pipeline's disputed regions; this
# serves a vLLM model and transcribes every crop (two-pass adaptive budget),
# then tars reads/. Enumeration and arbitration stay local.
#
#   bash run.sh smoke     # first N crops → confirm the plumbing FAST
#   bash run.sh full      # LightOnOCR-2 over all crops (the real run)
#
# Unlike the other kits this one takes CROPS, not pages: crops/<key>.png plus
# manifest.jsonl of {key, expect, area}. See README for the contract.
#
# Recommended image: a recent vllm/vllm-openai (vLLM ≥0.24 needs transformers v5;
# do NOT pin transformers here).
#
# Multi-GPU: `GPUS=n bash run.sh full` runs one worker per card over disjoint
# crop slices and packages reads/ once. GPUS defaults to every GPU on the pod.
#
# Tunables (env): GPUS (all), PORT (8000 — base port; worker i uses PORT+i),
# CONCURRENCY (32), SMOKE_N (8).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
MODE="${1:-full}"
PORT="${PORT:-8000}"
CONCURRENCY="${CONCURRENCY:-32}"
MODEL="lightonai/LightOnOCR-2-1B"     # real model in both modes
SET="$(cat SET 2>/dev/null || echo out)"
SHARD="${SHARD:-0/1}"
OUT_DIR=reads
TAR="lighton_reads_${SET}.tar.gz"
# One worker per GPU (no-op on a single card); never returns in the parent. The
# smoke pass stays single-worker: its job is to prove the plumbing fast.
[[ "$MODE" == smoke ]] || { source "$ROOT/fanout.sh"; gpu_fanout lighton; }
# smoke = same model, just the first N crops → confirms serve+batch+output before
# the full run (the model downloads once and is cached/warm for `full`).
[ "$MODE" = smoke ] && EXTRA="--smoke ${SMOKE_N:-8}" || EXTRA=""

# Guarded: a missing crops/ makes `find` exit 1, `pipefail` carries that out of
# the pipeline, and `set -e` kills the script silently — before the error below
# ever prints, which is exactly the message you would need.
NCROP=0; [[ -d crops ]] && NCROP=$(find crops -maxdepth 1 -name '*.png' -not -name '._*' | wc -l | tr -d ' ')
[[ "$NCROP" -gt 0 && -s manifest.jsonl ]] || {
  echo "!! crops/ or manifest.jsonl missing — build them locally first (README)" >&2
  exit 1; }
echo ">> [lighton] $NCROP crops, set=$SET, mode=$MODE"

if [[ "${SKIP_DEPS:-0}" == 1 ]]; then
  echo ">> [lighton] SKIP_DEPS=1 — another worker installed the deps"
else
  echo ">> [lighton] deps"
  command -v vllm >/dev/null || python -m pip install -q vllm
  python -m pip install -q openai hf_transfer
  [[ -n "${_FANOUT_CHILD:-}" ]] && deps_ready
fi
export HF_HUB_ENABLE_HF_TRANSFER=1

# Preflight: the #1 pod failure is a host-driver / torch-CUDA mismatch (vLLM's
# torch built for a newer CUDA than the host driver supports). Fail fast with a
# one-line diagnosis instead of a wall of engine-core traceback.
echo ">> [lighton] preflight: driver vs torch CUDA"
python - <<'PY' || { echo "!! CUDA preflight FAILED — pick a pod whose host CUDA >= torch's built CUDA (RunPod 'CUDA Version' badge), or reinstall a matching-cu torch. See README." >&2; exit 1; }
import torch, sys
built = torch.version.cuda
print(f"   torch {torch.__version__} built for CUDA {built}")
if not torch.cuda.is_available():
    print("   torch.cuda.is_available() == False", file=sys.stderr); sys.exit(1)
print(f"   device: {torch.cuda.get_device_name(0)}")
PY

# Per-worker server log: every worker serves its own vLLM, and two instances in
# one directory would truncate and interleave serve.log.
SERVE_LOG=serve.log
[[ "$SHARD" != "0/1" ]] && SERVE_LOG="serve_shard${SHARD//\//of}.log"
echo ">> [lighton] serving $MODEL on :$PORT ($MODE, shard $SHARD)"
vllm serve "$MODEL" --limit-mm-per-prompt '{"image":1}' \
  --mm-processor-cache-gb 0 --no-enable-prefix-caching \
  --port "$PORT" > "$SERVE_LOG" 2>&1 &
SERVER=$!
trap 'kill "$SERVER" 2>/dev/null || true' EXIT

echo ">> [lighton] waiting for /health"
for _ in $(seq 1 180); do
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && { OK=1; break; }
  kill -0 "$SERVER" 2>/dev/null || { echo "!! server exited:"; tail -40 "$SERVE_LOG"; exit 1; }
  sleep 5
done
[[ "${OK:-}" == 1 ]] || { echo "!! server never healthy:"; tail -40 "$SERVE_LOG"; exit 1; }

echo ">> [lighton] transcribing (concurrency $CONCURRENCY) $EXTRA"
python vllm_batch.py --base-url "http://localhost:$PORT/v1" \
  --model "$MODEL" --concurrency "$CONCURRENCY" --shard "$SHARD" $EXTRA

if [ "$MODE" = smoke ]; then
  echo ">> SMOKE OK — served, batched, and wrote $(ls reads | wc -l) reads. Ready for: bash run.sh full"
  exit 0
fi
# A fan-out child leaves packaging to the parent, which tars the shared reads/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out lighton
