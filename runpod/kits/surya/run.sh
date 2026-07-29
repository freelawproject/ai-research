#!/usr/bin/env bash
# Surya OCR 2 full-page OCR via vLLM — POD-SIDE runner.
# Serves datalab-to/surya-ocr-2 with vLLM, waits for /health, points the `surya`
# client at it (SURYA_INFERENCE_URL), OCRs every staged page, tars the output.
#
# ── vLLM VERSION MATTERS ──────────────────────────────────────────────────────
# surya-ocr-2's arch is `Qwen3_5ForConditionalGeneration`, which is ONLY
# registered in vLLM >= 0.20.1 (Datalab pins vllm/vllm-openai:v0.20.1). vLLM
# 0.11.0 — the dots kit's image — rejects it with "Model architectures
# ['Qwen3_5ForConditionalGeneration'] are not supported". surya-ocr itself has
# NO vllm dependency and registers NO plugin (its vLLM backend is just an OpenAI
# client), so the arch must be native to the running vLLM.
#
#   >>> LAUNCH THE POD FROM IMAGE:  vllm/vllm-openai:v0.20.1  <<<
#
# On that image vllm is already 0.20.1 and `pip install surya-ocr` is consistent
# (both want transformers 5.x / torch 2.x). If you must run on an older-vLLM pod,
# set ALLOW_VLLM_UPGRADE=1 to pip-install vllm==0.20.1 (may hit a torch/CUDA
# mismatch — the v0.20.1 image is the clean path).
#
#   runpodctl receive <code>
#   tar xzf surya_<set>_kit.tar.gz && cd surya
#   bash run.sh                       # serve + infer + tar out/
#   runpodctl send surya_out_<set>.tar.gz
#
# Serve flags mirror Datalab's own launcher (surya/inference/backends/vllm.py):
# --dtype bfloat16 --max-model-len 18000 --gpu-memory-utilization 0.85
# --mm-processor-kwargs '{"min_pixels":3136,"max_pixels":6291456}'
# --enable-prefix-caching. MTP speculative decoding (Datalab default, a decode
# speedup only) is OFF here; set ENABLE_MTP=1 to match.
#
# Variant (env MODE): block (default, full-page layout blocks) or line
# (detection lines OCR'd individually). Outputs + tarball are mode-suffixed so
# the two variants never collide:  MODE=line bash run.sh
#
# MULTI-GPU on one pod — one worker per GPU, disjoint pages, one command:
#
#   GPUS=2 bash run.sh          # workers on cards 0 and 1, shards 0/2 and 1/2
#
# GPUS defaults to every GPU the pod exposes. The workers write into the same
# out/ (disjoint stems, atomic writes) and the parent packages it once they have
# all finished. Data-parallel like this beats --tensor-parallel-size 2 for
# throughput; TP adds cross-GPU traffic per token and does not double pages/sec.
#
# Tunables (env): GPUS (all), MODE (block), PORT (8000 — base port; worker i
# uses PORT+i), MODEL_ID (datalab-to/surya-ocr-2), GPU_MEM_UTIL (0.85),
# MAX_MODEL_LEN (18000), PARALLEL (8), BATCH (0=auto), ENABLE_MTP (0),
# ALLOW_VLLM_UPGRADE (0).
#
# THROUGHPUT: budget ~3 s per page per GPU (~1,200 pages/GPU-hour). Full-page OCR
# emits thousands of output tokens per page, so this is decode-bound — limited by
# memory bandwidth, not compute. To go faster, add GPUs (GPUS=n) rather than
# buying a card with more compute. GPU "utilisation" reads ~100%
# regardless; it counts kernels running, not saturation.
# Per-GPU levers, cheapest first:
#   PARALLEL=16 BATCH=16 bash run.sh    # in-flight depth = min(BATCH, PARALLEL)
#   ENABLE_MTP=1                        # speculative decode, Datalab's default
# Re-running is resume-safe, so a settings change can be A/B'd on the remaining
# pages of a live run: Ctrl-C, re-run with new env, compare the pg/s line.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"; cd "$ROOT"
MODE="${MODE:-block}"
PORT="${PORT:-8000}"
MODEL_ID="${MODEL_ID:-datalab-to/surya-ocr-2}"
GPU_MEM_UTIL="${GPU_MEM_UTIL:-0.85}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-18000}"
PARALLEL="${PARALLEL:-8}"
# Images per predictor call. In-flight requests are min(BATCH, PARALLEL), so
# raising one alone does nothing — this workload is decode-bound and wants both
# raised together. 0 = the client's auto (block 8, line 1).
BATCH="${BATCH:-0}"
ENABLE_MTP="${ENABLE_MTP:-0}"
ALLOW_VLLM_UPGRADE="${ALLOW_VLLM_UPGRADE:-0}"
SHARD="${SHARD:-0/1}"
# Two instances in ONE directory (one per GPU) would otherwise collide: the pip
# installs race in a shared site-packages, both servers grab the same port, and
# write into one site-packages. The fan-out sets SKIP_DEPS on every worker but
# the first, and they wait for its marker before starting.
SKIP_DEPS="${SKIP_DEPS:-0}"
SET="$(cat SET 2>/dev/null || echo out)"
[[ "$MODE" == line ]] && { OUT_DIR=out_line; TAR="surya_out_line_${SET}.tar.gz"; } \
                      || { OUT_DIR=out;      TAR="surya_out_${SET}.tar.gz"; }
# One worker per GPU (no-op on a single card); never returns in the parent.
source "$ROOT/fanout.sh"
gpu_fanout "surya:$MODE"

# Workers share OUT_DIR (disjoint stems, atomic writes) and the parent packages
# it once. They must NOT share a server log: every worker serves its own vLLM,
# and two instances in one directory would truncate and interleave serve.log —
# destroying the evidence you need to tell whether card 1's server ever came up.
SERVE_LOG=serve.log
[[ "$SHARD" != "0/1" ]] && SERVE_LOG="serve_shard${SHARD//\//of}.log"

# Count what exists, and ONLY what exists: a missing directory makes `find` exit
# 1, `pipefail` carries that status out of the pipeline, and `set -e` then kills
# the script — with find's stderr discarded, silently and before any output. On a
# fresh extract images/ does not exist yet, so this is the normal path.
NIMG=0; [[ -d images ]] && NIMG=$(find images -maxdepth 1 -name '*.png' -not -name '._*' | wc -l | tr -d ' ')
[[ "$NIMG" -gt 0 ]] || {
  echo "!! no page images staged — images/ is built by pack.sh, so this kit" >&2
  echo "!! was packed wrong or unpacked incompletely" >&2
  exit 1; }
echo ">> [surya] set=$SET, mode=$MODE, shard=$SHARD ($NIMG page images)"

if [[ "$SKIP_DEPS" == 1 ]]; then
  echo ">> [surya] SKIP_DEPS=1 — another worker installed the deps"
else
  echo ">> [surya] deps (surya-ocr + client libs)"
  python -m pip install -q surya-ocr pillow pymupdf hf_transfer
  deps_ready
fi

echo ">> [surya] checking vLLM >= 0.20.1 (arch Qwen3_5ForConditionalGeneration)"
if ! python - <<'PY'
import sys
try:
    import vllm
    from packaging.version import Version
    sys.exit(0 if Version(vllm.__version__) >= Version("0.20.1") else 1)
except Exception:
    sys.exit(1)
PY
then
  CUR="$(python -c 'import vllm,sys; sys.stdout.write(getattr(vllm,"__version__","none"))' 2>/dev/null || echo none)"
  if [[ "$ALLOW_VLLM_UPGRADE" == 1 ]]; then
    echo ">> [surya] vLLM $CUR too old — upgrading to 0.20.1 (ALLOW_VLLM_UPGRADE=1)"
    python -m pip install -q -U "vllm==0.20.1"
  else
    echo "!! vLLM $CUR does NOT support surya-ocr-2's arch (need >= 0.20.1)."
    echo "!! Fix: launch the pod from image  vllm/vllm-openai:v0.20.1"
    echo "!! or re-run with  ALLOW_VLLM_UPGRADE=1 bash run.sh  (may hit CUDA mismatch)."
    exit 1
  fi
fi
echo ">> [surya] vLLM ok: $(python -c 'import vllm;print(vllm.__version__)')"

SERVE_ARGS=(
  "$MODEL_ID"
  --dtype bfloat16
  --max-model-len "$MAX_MODEL_LEN"
  --gpu-memory-utilization "$GPU_MEM_UTIL"
  --mm-processor-kwargs '{"min_pixels": 3136, "max_pixels": 6291456}'
  --enable-prefix-caching
  --port "$PORT"
)
[[ "$ENABLE_MTP" == 1 ]] && SERVE_ARGS+=(--speculative-config '{"method": "mtp", "num_speculative_tokens": 2}')

# Check BEFORE announcing a start, so the failure does not read as "started, then
# refused". A port already in use means this server cannot bind, dies, and
# otherwise surfaces as a generic "server exited early" with that GPU at 0%.
# Usually a LEFTOVER server from an earlier run: the EXIT trap below misses it if
# the shell was SIGKILLed or closed, and vLLM's engine-core child can outlive its
# parent while still holding the GPU.
# Test BINDABILITY, not /health: a leftover server that is still loading weights
# answers /health with a non-200, so a curl probe would call the port free and we
# would be right back to the confusing "server exited early".
PORT_STATE="$(python - "$PORT" <<'PY'
import socket
import sys
s = socket.socket()
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
    print("free")
except OSError:
    print("busy")
finally:
    s.close()
PY
)"
if [[ "$PORT_STATE" == busy ]]; then
  if curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1; then
    echo "!! port $PORT already has a HEALTHY server on it." >&2
  else
    echo "!! port $PORT is in use (by something not answering /health — likely a" >&2
    echo "!! server still loading, or a half-dead one)." >&2
  fi
  echo "!! Most likely a leftover from an earlier run, NOT a mistake in this" >&2
  echo "!! command." >&2
  echo "!! Identify it (which PID, which physical GPU), then stop just that one:" >&2
  echo "!!   bash diag_gpus.sh" >&2
  echo "!!   ss -ltnp | grep ':$PORT'      # or: netstat -ltnp | grep ':$PORT'" >&2
  echo "!!   kill <pid>                    # do NOT pkill -f 'vllm serve' if" >&2
  echo "!!                                 # another GPU's run is still live" >&2
  echo "!! Or serve on a free port instead:  PORT=$((PORT + 1)) ... bash run.sh" >&2
  exit 1
fi

echo ">> [surya] starting vLLM server: $MODEL_ID on :$PORT"
echo ">> [surya] server log → $SERVE_LOG"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" vllm serve "${SERVE_ARGS[@]}" > "$SERVE_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

# Convert while the server pulls and loads weights — both take minutes, and
# rendering is pure CPU, so the two overlap for free.
echo ">> [surya] $NIMG page images ready"

echo ">> [surya] waiting for /health (weights pull + load, ~3-8 min)"
for _ in $(seq 1 240); do
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && { HEALTHY=1; break; }
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "!! server exited early:"; tail -60 "$SERVE_LOG"; exit 1; }
  sleep 5
done
[[ "${HEALTHY:-}" == 1 ]] || { echo "!! server never became healthy:"; tail -60 "$SERVE_LOG"; exit 1; }
echo ">> [surya] server healthy"

export SURYA_INFERENCE_BACKEND=vllm
export SURYA_INFERENCE_URL="http://localhost:$PORT/v1"
export SURYA_INFERENCE_PARALLEL="$PARALLEL"
# State the binding out loud: with one instance per GPU the single most common
# failure is two clients pointed at one server, and that is invisible unless the
# GPU and the URL are printed side by side in each shell.
echo ">> [surya] GPU ${CUDA_VISIBLE_DEVICES:-0} | server $SURYA_INFERENCE_URL"\
     "| shard $SHARD | batch ${BATCH:-auto} | parallel $PARALLEL"

# Smoke-test one page first: the surya client API (esp. the line-mode detection
# bridge) is the main unknown on a fresh pod — fail loud on page 1 rather than
# after a long batch. On a multi-thousand-page set that is the difference
# between losing 30 seconds and losing an hour of GPU time.
echo ">> [surya:$MODE] smoke test (1 page)"
# NOT `| sort | head -1`: past ~1,500 paths sort blocks on a full pipe, head
# exits after its one line, sort takes SIGPIPE, and pipefail turns that into
# another silent death — size-dependent, so it would pass on 1k and fail on
# volumes. Slice the first line in the shell instead.
IMGS="$(find images -maxdepth 1 -name '*.png' -not -name '._*' | sort)"
FIRST="$(basename "${IMGS%%$'\n'*}" .png)"
python run_infer_surya.py --images-dir images --out-dir "$OUT_DIR" --mode "$MODE" \
  --stems "$FIRST" --batch-size 1
[[ -s "$OUT_DIR/$FIRST.json" ]] || { echo "!! smoke test produced no output — check the surya client call"; exit 1; }
echo ">> [surya:$MODE] smoke ok ($FIRST)"

echo ">> [surya:$MODE] inferring all pages"
python run_infer_surya.py --images-dir images --out-dir "$OUT_DIR" --mode "$MODE" \
  --shard "$SHARD" --batch-size "$BATCH"

# A fan-out child leaves packaging to the parent, which tars the shared out/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out "surya:$MODE"
