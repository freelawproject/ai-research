#!/usr/bin/env bash
# Multi-GPU fan-out, shared by every pod kit.
#
# A kit's run.sh is a SINGLE-SHARD worker: it serves one model on one GPU and
# processes its slice of the work. This turns one invocation into one worker per
# GPU, so the pod's cards are all busy without the operator launching anything
# by hand:
#
#   GPUS=4 bash run.sh          # four workers, shards 0/4 … 3/4
#   bash run.sh                 # GPUS defaults to every GPU the pod exposes
#   GPUS=1 bash run.sh          # one worker (also the CPU path)
#
# Source this near the top of a run.sh, after its env parsing, then call
# `gpu_fanout <label>`. In the parent the call never returns — it launches the
# children, waits, and exits with their combined status. In a child it returns
# immediately and the rest of run.sh proceeds as the single-shard worker it
# already was.
#
# What the parent hands each child:
#   CUDA_VISIBLE_DEVICES=i   one card, so vLLM cannot grab them all
#   PORT=<a free port>       its own server port, PROBED from PORT upward — a pod
#                            is not an empty machine (RunPod images run their own
#                            nginx; an earlier run can leave a vLLM holding a
#                            port), so base+i is assigned only if it is bindable.
#                            SKIP_PORTS=8001,8888 excludes ports from probing.
#   SHARD=i/n                its slice (clients take every n-th page)
#   SKIP_DEPS=1              for i>0: pip writes shared site-packages and must
#                            not run concurrently — child 0 installs, the rest
#                            wait for the marker it drops
#   NO_TAR=1                 the parent tars out/ ONCE, after every child is done
#
# Sharded work needs no merge step: the children write into the same out/, and
# their page slices are disjoint by construction.

# The caller's own CLI args, captured at source time (sourcing with no args
# leaves the caller's positional parameters in place). A child is re-invoked
# with THESE — inside gpu_fanout, "$@" would be the function's own args (the
# label), and lighton's `bash run.sh full` must stay `full` in every worker.
_FANOUT_ARGS=("$@")

# Package the output ONCE. A kit sets OUT_DIR and TAR before fanning out, so
# the parent can do this after its children finish; a single-worker run calls it
# at the end of run.sh instead. The macOS-artifact excludes match pack.sh —
# `._*` siblings would otherwise ride back to the local side as junk.
kit_tar_out() {
  local label="${1:-kit}" out="${OUT_DIR:-out}" tar="${TAR:?kit_tar_out needs TAR}"
  local n
  [[ -d "$out" ]] || {
    echo "!! [$label] no $out/ to package — inference produced nothing" >&2
    return 1; }
  n="$(find "$out" -type f -not -name '._*' | wc -l | tr -d ' ')"
  rm -f "$tar"
  COPYFILE_DISABLE=1 tar czf "$tar" -C . \
    --exclude '.DS_Store' --exclude '._*' --exclude '__pycache__' \
    --exclude '*.part' "$out"
  echo ">> [$label] $n output file(s) -> $tar ($(du -h "$tar" | cut -f1))"
  echo ">> send back:  runpodctl send $(pwd)/$tar"
}

# Free = BINDABLE. Not /health: a leftover server still loading its weights
# answers /health with a non-200, so a curl probe would call its port free and
# the next server would die on bind instead. SKIP_PORTS is never probed at all —
# an escape hatch for a port known to be someone else's.
port_free() {
  local port="${1:?port_free needs a port}"
  case ",${SKIP_PORTS:-}," in *",$port,"*) return 1 ;; esac
  python - "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

s = socket.socket()
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
finally:
    s.close()
PY
}

# n distinct bindable ports from base upward, one per line, stepping over
# whatever the pod already runs. Skips are announced: a silently reassigned port
# makes the address a worker logs impossible to predict, and the whole point of
# probing is that the operator can see which ports were taken.
alloc_ports() {
  local want="${1:?}" port="${2:?}" label="${3:-kit}"
  local -a found=()
  local tried=0
  while [[ "${#found[@]}" -lt "$want" ]]; do
    if [[ "$tried" -ge 64 ]]; then
      echo "!! [$label] no $want free ports in $2-$(($2 + 63))" >&2
      return 1
    fi
    if port_free "$port"; then
      found+=("$port")
    else
      echo ">> [$label] port $port in use — skipping" >&2
    fi
    port=$((port + 1))
    tried=$((tried + 1))
  done
  printf '%s\n' "${found[@]}"
}

# A worker's own last check before it serves. The fan-out hands out ports it
# probed, but seconds pass before vLLM binds them, and a SINGLE-worker run never
# goes through the fan-out at all. This one fails loudly instead of moving: a
# busy port is usually a leftover server from an earlier run, that server is
# still holding a GPU, and quietly serving beside it leaves both runs slow and
# neither obviously wrong.
require_port() {
  local label="${1:?}" port="${2:?}"
  port_free "$port" && return 0
  if curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
    echo "!! [$label] port $port already has a HEALTHY server on it." >&2
  else
    echo "!! [$label] port $port is in use, by something not answering /health" >&2
    echo "!! (a server still loading weights, a half-dead one, or a service that" >&2
    echo "!! is not vLLM at all — pod images run their own nginx)." >&2
  fi
  echo "!! Identify it (which PID, which physical GPU), then stop just that one:" >&2
  echo "!!   ss -ltnp | grep ':$port'      # or: netstat -ltnp | grep ':$port'" >&2
  echo "!!   kill <pid>                    # do NOT pkill -f 'vllm serve' if" >&2
  echo "!!                                 # another GPU's run is still live" >&2
  echo "!! Or serve elsewhere:   PORT=$((port + 1)) bash run.sh" >&2
  echo "!! Or keep the fan-out off it:  SKIP_PORTS=$port GPUS=n bash run.sh" >&2
  return 1
}

# /health only proves SOMETHING answers on the port. Ask /v1/models and require
# the model this kit asked for, because an nginx satisfies /health and then
# answers the first real request with `405 Not Allowed` — which is how a
# port collision reads as "server healthy" followed by every page failing.
require_vllm() {
  local label="${1:?}" port="${2:?}" want="${3:?}" body
  body="$(curl -sf "http://localhost:$port/v1/models" 2>/dev/null || true)"
  if BODY="$body" WANT="$want" python - <<'PY'
import json
import os
import sys

try:
    served = [m.get("id") for m in json.loads(os.environ["BODY"])["data"]]
except Exception:
    sys.exit(1)
sys.exit(0 if os.environ["WANT"] in served else 1)
PY
  then
    return 0
  fi
  echo "!! [$label] :$port answered /health but is NOT serving '$want'." >&2
  echo "!! Something else holds the port. /v1/models said:" >&2
  echo "!!   ${body:-<no response>}" >&2
  echo "!! Find the occupant:  ss -ltnp | grep ':$port'" >&2
  echo "!! Then either stop it, or keep this kit off that port:" >&2
  echo "!!   SKIP_PORTS=$port GPUS=n bash run.sh" >&2
  return 1
}

# Number of GPUs to use: GPUS if set, else every GPU nvidia-smi reports, else 1
# (a CPU pod, or a box without the driver — the kits all still run).
_gpu_count() {
  local n
  n="$(nvidia-smi -L 2>/dev/null | grep -c '^GPU ' || true)"
  [[ -n "$n" && "$n" -gt 0 ]] || n=1
  printf '%s' "$n"
}

gpu_fanout() {
  local label="${1:?gpu_fanout needs a label}"
  local gpus="${GPUS:-$(_gpu_count)}"

  # A child does the actual work: return and let run.sh carry on.
  [[ -n "${_FANOUT_CHILD:-}" ]] && return 0
  if [[ "$gpus" -le 1 ]]; then
    echo ">> [$label] 1 GPU (GPUS=$gpus) — single worker"
    return 0
  fi

  local stagger="${DEPS_STAGGER:-15}"
  local self="$0"
  echo ">> [$label] $gpus GPUs — one worker per card, shards 0/$gpus … $((gpus - 1))/$gpus"
  echo ">> [$label] child 0 installs deps; the rest wait on .deps_ready"
  rm -f .deps_ready .deps_failed

  # Server ports, probed rather than assumed: only kits that serve a model set
  # PORT at all, so an unset one (the layout kit) allocates nothing.
  # `while read`, not `mapfile`: mapfile is bash 4+, and a kit should not care
  # which bash a pod image ships.
  local -a ports=()
  local p
  if [[ -n "${PORT:-}" ]]; then
    while IFS= read -r p; do ports+=("$p"); done \
      < <(alloc_ports "$gpus" "$PORT" "$label")
    [[ "${#ports[@]}" == "$gpus" ]] || exit 1
    echo ">> [$label] server ports: ${ports[*]}"
  fi

  local -a pids=() logs=()
  local i log
  for ((i = 0; i < gpus; i++)); do
    log="worker_shard${i}of${gpus}.log"
    logs+=("$log")
    (
      # Child 0 installs; every other child waits for its marker rather than
      # racing pip into the same site-packages.
      if [[ "$i" -gt 0 ]]; then
        local waited=0
        while [[ ! -f .deps_ready && "$waited" -lt 1800 ]]; do
          # Child 0 can die BEFORE installing anything — a kit packed without
          # images exits at its own staging guard — and then nothing would ever
          # drop the marker. Without this the other workers burn the full 1800s
          # in a sleep loop and the run looks alive while doing nothing.
          [[ -f .deps_failed ]] && {
            echo "!! [$label] shard 0 failed before the deps install;" \
                 "see worker_shard0of${gpus}.log" >&2
            exit 1; }
          sleep "$stagger"
          waited=$((waited + stagger))
        done
      fi
      local crc=0
      CUDA_VISIBLE_DEVICES="$i" \
      PORT="${ports[$i]:-}" \
      SHARD="$i/$gpus" \
      SKIP_DEPS="$([[ "$i" -gt 0 ]] && echo 1 || echo "${SKIP_DEPS:-0}")" \
      NO_TAR=1 \
      _FANOUT_CHILD=1 \
        bash "$self" ${_FANOUT_ARGS[@]+"${_FANOUT_ARGS[@]}"} > "$log" 2>&1 || crc=$?
      # Release the waiters on child 0's failure, not just its success.
      [[ "$crc" == 0 || "$i" -gt 0 || -f .deps_ready ]] || : > .deps_failed
      exit "$crc"
    ) &
    pids+=("$!")
  done

  local rc=0 n=0
  for i in "${!pids[@]}"; do
    if wait "${pids[$i]}"; then
      echo ">> [$label] shard $i/$gpus OK  (${logs[$i]})"
      n=$((n + 1))
    else
      rc=1
      echo "!! [$label] shard $i/$gpus FAILED — last lines of ${logs[$i]}:" >&2
      tail -20 "${logs[$i]}" >&2
    fi
  done
  rm -f .deps_ready .deps_failed
  echo ">> [$label] $n/$gpus shard(s) finished cleanly"
  # out/ is shared and the slices are disjoint, so there is nothing to merge —
  # package it once, from the parent, exactly as a single-GPU run would.
  [[ "$rc" == 0 ]] && kit_tar_out "$label"
  exit "$rc"
}

# Deps marker: child 0 calls this once its install has finished, releasing the
# others. A single-worker run calls it too and nobody is waiting — harmless.
deps_ready() { : > .deps_ready; }
