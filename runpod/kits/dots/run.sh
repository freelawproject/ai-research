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
# Tunables (env): GPUS (all), PORT (8000 — the base; each worker gets its own
# free port from there upward), SKIP_PORTS (ports to leave alone),
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

# Workers share out/ (disjoint stems, atomic writes) and the parent packages it
# once. They must NOT share a server log: every worker serves its own vLLM, and
# two instances in one directory truncate and interleave serve.log — destroying
# the evidence you need to tell whether card 1's server ever came up.
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
echo ">> [dots-vllm] set=$SET, shard=$SHARD ($NIMG page images)"

if [[ "${SKIP_DEPS:-0}" == 1 ]]; then
  echo ">> [dots-vllm] SKIP_DEPS=1 — another worker installed the deps"
else
  echo ">> [dots-vllm] deps (client + vllm if absent)"
  command -v vllm >/dev/null || python -m pip install -q "vllm==0.11.0"
  # hf_transfer: base images set HF_HUB_ENABLE_HF_TRANSFER=1, which hard-fails
  # the model download unless the package is present (also speeds up the ~6GB
  # pull).
  python -m pip install -q openai hf_transfer

  # transformers: vLLM 0.11.0 needs the 4.57 series, but its dep spec has no
  # upper cap, so a base image's transformers 5.x survives the vllm install
  # and breaks the tokenizer (Qwen2Tokenizer.all_special_tokens_extended).
  # Reinstall only when the installed version is out of range — and never pin
  # ==4.57.0, which is yanked on PyPI; 4.57.1+ are fine.
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

# Preflight: the #1 pod failure is a host-driver / torch-CUDA mismatch (vLLM's
# torch built for a newer CUDA than the host driver supports). Fail fast with
# a one-line diagnosis instead of a wall of engine-core traceback.
echo ">> [dots-vllm] preflight: driver vs torch CUDA"
python - <<'PY' || { echo "!! CUDA preflight FAILED — pick a pod whose host CUDA >= torch's built CUDA (RunPod 'CUDA Version' badge), or reinstall a matching-cu torch. See README." >&2; exit 1; }
import torch, sys
built = torch.version.cuda
print(f"   torch {torch.__version__} built for CUDA {built}")
if not torch.cuda.is_available():
    print("   torch.cuda.is_available() == False", file=sys.stderr); sys.exit(1)
print(f"   device: {torch.cuda.get_device_name(0)}")
PY

# Check BEFORE announcing a start, so the failure does not read as "started,
# then refused". A port already in use means this server cannot bind and dies.
require_port dots-vllm "$PORT" || exit 1

echo ">> [dots-vllm] starting server: $MODEL_ID on :$PORT"
echo ">> [dots-vllm] server log → $SERVE_LOG"
CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-0}" \
vllm serve "$MODEL_ID" \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization "$GPU_MEM_UTIL" \
  --chat-template-content-format string \
  --served-model-name model \
  --trust-remote-code \
  --port "$PORT" > "$SERVE_LOG" 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" 2>/dev/null || true' EXIT

echo ">> [dots-vllm] $NIMG page images ready"

echo ">> [dots-vllm] waiting for /health (weights download + load, ~2-5 min)"
for _ in $(seq 1 180); do
  # Liveness BEFORE health: a server that died on bind leaves the port to
  # whoever else holds it, and probing health first would take that stranger's
  # 200 as our own and report a healthy server that does not exist.
  kill -0 "$SERVER_PID" 2>/dev/null || { echo "!! server exited early:"; tail -40 "$SERVE_LOG"; exit 1; }
  curl -sf "http://localhost:$PORT/health" >/dev/null 2>&1 && { HEALTHY=1; break; }
  sleep 5
done
[[ "${HEALTHY:-}" == 1 ]] || { echo "!! server never became healthy:"; tail -40 "$SERVE_LOG"; exit 1; }
# --served-model-name above, so this is the id the client must ask for.
require_vllm dots-vllm "$PORT" model || { tail -40 "$SERVE_LOG"; exit 1; }
echo ">> [dots-vllm] server healthy"

# One page before the batch: on a multi-thousand-page volume set a bad prompt
# or a tokenizer mismatch should cost seconds, not an hour of GPU time.
echo ">> [dots-vllm] smoke test (1 page)"
# A page from THIS worker's OWN slice. One shared smoke page means every worker
# infers it and they race to write it — and worse, whichever loses the race
# finds the file already there and passes its smoke gate on a HEALTHY worker's
# output, sailing into the batch with a server that answers nothing. The client
# shards as stems[i::n], so index i is this worker's first page.
# NOT `| sort | head -1`: past ~1,500 paths sort blocks on a full pipe, head
# exits after its one line, sort takes SIGPIPE, and pipefail turns that into
# another silent death — size-dependent, so it passes on a small set and fails
# on a volume set. Read the list into an array instead (`while read`, not
# `mapfile`: mapfile is bash 4+, and a kit should not care which bash a pod
# image ships).
IMGS=()
while IFS= read -r p; do IMGS+=("$p"); done \
  < <(find images -maxdepth 1 -name '*.png' -not -name '._*' | sort)
SHARD_I="${SHARD%%/*}"
FIRST=""
[[ -n "${IMGS[$SHARD_I]:-}" ]] && FIRST="$(basename "${IMGS[$SHARD_I]}" .png)"
if [[ -z "$FIRST" ]]; then
  echo ">> [dots-vllm] shard $SHARD holds no pages — nothing to smoke"
else
  python run_infer_dots_vllm.py --images-dir images --out-dir out \
    --base-url "http://localhost:$PORT/v1" --model model \
    --concurrency 1 --stems "$FIRST"
  [[ -s "out/$FIRST.json" ]] || { echo "!! smoke test produced no output"; exit 1; }
  echo ">> [dots-vllm] smoke ok ($FIRST)"
fi

echo ">> [dots-vllm] inferring (concurrency $CONCURRENCY)"
python run_infer_dots_vllm.py --images-dir images --out-dir out \
  --base-url "http://localhost:$PORT/v1" --model model \
  --concurrency "$CONCURRENCY" --shard "$SHARD"

# A fan-out child leaves packaging to the parent, which tars the shared out/
# once every worker has finished.
[[ -n "${NO_TAR:-}" ]] || kit_tar_out dots
