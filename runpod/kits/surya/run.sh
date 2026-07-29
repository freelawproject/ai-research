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
# Tunables (env): GPUS (all), MODE (block), PORT (8000 — the base; each worker
# gets its own free port from there upward), SKIP_PORTS (ports to leave alone),
# MODEL_ID (datalab-to/surya-ocr-2), GPU_MEM_UTIL (0.85),
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

# The vLLM version gate lives INSIDE the deps turn: with GPUS=n every
# worker shares one site-packages, and two concurrent pips uninstall
# packages from under each other. The deps worker checks AND (with
# ALLOW_VLLM_UPGRADE=1) upgrades before deps_ready releases the
# waiters; a SKIP_DEPS worker only VERIFIES — it never runs pip.
vllm_ok() {
  python - <<'PY'
import sys
try:
    import vllm
    from packaging.version import Version
    sys.exit(0 if Version(vllm.__version__) >= Version("0.20.1") else 1)
except Exception:
    sys.exit(1)
PY
}

if [[ "$SKIP_DEPS" == 1 ]]; then
  echo ">> [surya] SKIP_DEPS=1 — another worker installed the deps"
  vllm_ok || {
    echo "!! vLLM >= 0.20.1 still not importable — the deps worker was" >&2
    echo "!! supposed to leave it installed; see its worker_shard*.log" >&2
    exit 1; }
else
  echo ">> [surya] deps (surya-ocr + client libs)"
  python -m pip install -q surya-ocr pillow hf_transfer
  echo ">> [surya] checking vLLM >= 0.20.1 (arch Qwen3_5ForConditionalGeneration)"
  if ! vllm_ok; then
    CUR="$(python -c 'import vllm,sys; sys.stdout.write(getattr(vllm,"__version__","none"))' 2>/dev/null || echo none)"
    if [[ "$ALLOW_VLLM_UPGRADE" == 1 ]]; then
      echo ">> [surya] vLLM $CUR too old — upgrading to 0.20.1 (ALLOW_VLLM_UPGRADE=1)"
      python -m pip install -q -U "vllm==0.20.1"
      vllm_ok || {
        echo "!! vLLM 0.20.1 installed but still not importable — see the" >&2
        echo "!! pip output above (a broken base env, not a version issue)" >&2
        exit 1; }
    else
      echo "!! vLLM $CUR does NOT support surya-ocr-2's arch (need >= 0.20.1)."
      echo "!! Fix: launch the pod from image  vllm/vllm-openai:v0.20.1"
      echo "!! or re-run with  ALLOW_VLLM_UPGRADE=1 bash run.sh  (may hit CUDA mismatch)."
      exit 1
    fi
  fi
  deps_ready
fi
echo ">> [surya] vLLM ok: $(python -c 'import vllm;print(vllm.__version__)')"

# Preflight: the #1 pod failure is a host-driver / torch-CUDA mismatch (vLLM's
# torch built for a newer CUDA than the host driver supports). Fail fast with
# a one-line diagnosis instead of a wall of engine-core traceback.
echo ">> [surya] preflight: driver vs torch CUDA"
python - <<'PY' || { echo "!! CUDA preflight FAILED — pick a pod whose host CUDA >= torch's built CUDA (RunPod 'CUDA Version' badge), or reinstall a matching-cu torch. See README." >&2; exit 1; }
import torch, sys
built = torch.version.cuda
print(f"   torch {torch.__version__} built for CUDA {built}")
if not torch.cuda.is_available():
    print("   torch.cuda.is_available() == False", file=sys.stderr); sys.exit(1)
print(f"   device: {torch.cuda.get_device_name(0)}")
PY

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
# parent while still holding the GPU. (fanout.sh owns the check — every kit
# serves the same way and needs the same diagnosis.)
require_port surya "$PORT" || exit 1

echo ">> [surya] starting vLLM server: $MODEL_ID on :$PORT"
echo ">> [surya] server log → $SERVE_LOG"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" vllm serve "${SERVE_ARGS[@]}" > "$SERVE_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

echo ">> [surya] $NIMG page images ready"

echo ">> [surya] waiting for /health (weights pull + load, ~3-8 min)"
for _ in $(seq 1 240); do
  # Liveness BEFORE health: a server that died on bind leaves the port to
  # whoever else holds it, and probing health first would take that stranger's
  # 200 as our own and report a healthy server that does not exist.
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "!! server exited early:"; tail -60 "$SERVE_LOG"; exit 1; }
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && { HEALTHY=1; break; }
  sleep 5
done
[[ "${HEALTHY:-}" == 1 ]] || { echo "!! server never became healthy:"; tail -60 "$SERVE_LOG"; exit 1; }
# No --served-model-name, so vLLM serves it under the model id itself.
require_vllm surya "$PORT" "$MODEL_ID" || { tail -60 "$SERVE_LOG"; exit 1; }
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
# A page from THIS worker's OWN slice. One shared smoke page means every worker
# infers it and they race to write it — and worse, whichever loses the race
# finds the file already there and passes its smoke gate on a HEALTHY worker's
# output, sailing into the batch with a server that answers nothing. The client
# shards as stems[i::n], so index i is this worker's first page.
# NOT `| sort | head -1`: past ~1,500 paths sort blocks on a full pipe, head
# exits after its one line, sort takes SIGPIPE, and pipefail turns that into
# another silent death — size-dependent, so it would pass on 1k and fail on
# volumes. Read the list into an array instead (`while read`, not `mapfile`:
# mapfile is bash 4+, and a kit should not care which bash a pod image ships).
IMGS=()
while IFS= read -r p; do IMGS+=("$p"); done \
  < <(find images -maxdepth 1 -name '*.png' -not -name '._*' | sort)
SHARD_I="${SHARD%%/*}"
FIRST=""
[[ -n "${IMGS[$SHARD_I]:-}" ]] && FIRST="$(basename "${IMGS[$SHARD_I]}" .png)"
if [[ -z "$FIRST" ]]; then
  echo ">> [surya:$MODE] shard $SHARD holds no pages — nothing to smoke"
else
  python run_infer_surya.py --images-dir images --out-dir "$OUT_DIR" --mode "$MODE" \
    --stems "$FIRST" --batch-size 1
  [[ -s "$OUT_DIR/$FIRST.json" ]] || { echo "!! smoke test produced no output — check the surya client call"; exit 1; }
  echo ">> [surya:$MODE] smoke ok ($FIRST)"
fi

echo ">> [surya:$MODE] inferring all pages"
python run_infer_surya.py --images-dir images --out-dir "$OUT_DIR" --mode "$MODE" \
  --shard "$SHARD" --batch-size "$BATCH"

# A fan-out child leaves packaging to the parent, which tars the shared out/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out "surya:$MODE"
