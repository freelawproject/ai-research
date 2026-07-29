#!/usr/bin/env bash
# dots.mocr whole-page inference via vLLM — POD-SIDE runner.
# Starts a vLLM OpenAI server for dots.mocr, waits for /health, then fires the
# async client (vLLM continuous-batches server-side) over all pages, tars out/.
#
# RECOMMENDED POD IMAGE: vllm/vllm-openai:v0.11.0 (vllm preinstalled; dots.mocr
# is officially integrated as of vLLM 0.11.0 — no custom registration needed).
# Any CUDA 12.x image also works: run.sh pip-installs vllm==0.11.0 if missing.
#
#   runpodctl receive <code>
#   tar xzf dots_<set>_kit.tar.gz && cd dots
#   bash run.sh                       # serve + infer + tar out/
#   runpodctl send dots_out_<set>.tar.gz
#
# Multi-GPU: `GPUS=n bash run.sh` runs one worker per card (shards 0/n … n-1/n)
# and packages out/ once at the end. GPUS defaults to every GPU on the pod.
#
# Tunables (env): GPUS (all), PORT (8000 — the base port; worker i uses PORT+i),
# CONCURRENCY (32), MODEL_ID (rednote-hilab/dots.mocr), GPU_MEM_UTIL (0.9).
#
# Native transformers fallback (no vLLM): use run_native.sh instead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
PORT="${PORT:-8000}"
CONCURRENCY="${CONCURRENCY:-32}"
MODEL_ID="${MODEL_ID:-rednote-hilab/dots.mocr}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.9}"
SHARD="${SHARD:-0/1}"
SET="$(cat SET 2>/dev/null || echo out)"
OUT_DIR=out
TAR="dots_out_${SET}.tar.gz"
# One worker per GPU (no-op on a single card); never returns in the parent.
source "$ROOT/fanout.sh"
gpu_fanout dots

# Count what exists, and ONLY what exists: a missing directory makes `find` exit
# 1, `pipefail` carries that status out of the pipeline, and `set -e` then kills
# the script — with find's stderr discarded, silently and before any output. On a
# fresh extract images/ does not exist yet, so this is the normal path.
NIMG=0; [[ -d images ]] && NIMG=$(find images -maxdepth 1 -name '*.png' -not -name '._*' | wc -l | tr -d ' ')
[[ "$NIMG" -gt 0 ]] || {
  echo "!! no page images staged — images/ is built by pack.sh, so this kit" >&2
  echo "!! was packed wrong or unpacked incompletely" >&2
  exit 1; }
echo ">> [dots-vllm] set=$SET, shard=$SHARD ($NIMG page images)"

if [[ "${SKIP_DEPS:-0}" == 1 ]]; then
  echo ">> [dots-vllm] SKIP_DEPS=1 — another worker installed the deps"
else
echo ">> [dots-vllm] deps (client + vllm if absent)"
command -v vllm >/dev/null || python -m pip install -q "vllm==0.11.0"
# hf_transfer: base images set HF_HUB_ENABLE_HF_TRANSFER=1, which hard-fails the
# model download unless the package is present (also speeds up the ~6GB pull).
python -m pip install -q pymupdf pillow openai hf_transfer

# transformers: vLLM 0.11.0 needs the 4.57 series, but its dep spec has no upper
# cap, so a base image's transformers 5.x survives the vllm install and breaks
# the tokenizer (Qwen2Tokenizer.all_special_tokens_extended).
#
# Only intervene when the installed version is actually out of range. The
# v0.11.0 image already ships a good one, and force-installing over it is how
# this broke before: the old pin was `==4.57.0`, which PyPI has YANKED ("Error
# in the setup causing installation issues") — it replaced a working install
# with a broken one and the engine core failed to start. 4.57.1+ are fine.
python - <<'PY' || python -m pip install -q "transformers>=4.57.1,<4.58"
import sys
try:
    from packaging.version import Version
    import transformers
    v = Version(transformers.__version__)
    ok = Version("4.57.1") <= v < Version("4.58")
    print(f">> [dots-vllm] transformers {v} " + ("ok" if ok else "OUT OF RANGE"))
    sys.exit(0 if ok else 1)
except Exception as e:
    print(f">> [dots-vllm] transformers unusable ({e}) — reinstalling")
    sys.exit(1)
PY
  deps_ready
fi

echo ">> [dots-vllm] starting server: $MODEL_ID on :$PORT"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
vllm serve "$MODEL_ID" \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization "$GPU_MEM_UTIL" \
  --chat-template-content-format string \
  --served-model-name model \
  --trust-remote-code \
  --port "$PORT" > serve.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Convert while the server pulls and loads weights — both take minutes, and
# rendering is pure CPU, so the two overlap for free.
echo ">> [dots-vllm] $NIMG page images ready"

echo ">> [dots-vllm] waiting for /health (weights download + load, ~2-5 min)"
for _ in $(seq 1 180); do
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && { HEALTHY=1; break; }
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "!! server exited early:"; tail -40 serve.log; exit 1; }
  sleep 5
done
[[ "${HEALTHY:-}" == 1 ]] || { echo "!! server never became healthy:"; tail -40 serve.log; exit 1; }
echo ">> [dots-vllm] server healthy"

# One page before the batch: on a multi-thousand-page volume set a bad prompt
# or a tokenizer mismatch should cost seconds, not an hour of GPU time.
echo ">> [dots-vllm] smoke test (1 page)"
# NOT `| sort | head -1`: past ~1,500 paths sort blocks on a full pipe, head
# exits after its one line, sort takes SIGPIPE, and pipefail turns that into
# another silent death — size-dependent, so it passes on a small set and fails
# on a volume set. Slice the first line in the shell instead.
IMGS="$(find images -maxdepth 1 -name '*.png' -not -name '._*' | sort)"
FIRST="$(basename "${IMGS%%$'\n'*}" .png)"
python run_infer_dots_vllm.py --images-dir images --out-dir out \
  --base-url "http://localhost:$PORT/v1" --model model \
  --concurrency 1 --stems "$FIRST"
[[ -s "out/$FIRST.json" ]] || { echo "!! smoke test produced no output"; exit 1; }
echo ">> [dots-vllm] smoke ok ($FIRST)"

echo ">> [dots-vllm] inferring (concurrency $CONCURRENCY)"
python run_infer_dots_vllm.py --images-dir images --out-dir out \
  --base-url "http://localhost:$PORT/v1" --model model \
  --concurrency "$CONCURRENCY" --shard "$SHARD"

# A fan-out child leaves packaging to the parent, which tars the shared out/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out dots
