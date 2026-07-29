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
#   PORT=<base + i>          its own server port
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

  local base="${PORT:-8000}"
  local stagger="${DEPS_STAGGER:-15}"
  local self="$0"
  echo ">> [$label] $gpus GPUs — one worker per card, shards 0/$gpus … $((gpus - 1))/$gpus"
  echo ">> [$label] child 0 installs deps; the rest wait on .deps_ready"
  rm -f .deps_ready

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
          sleep "$stagger"
          waited=$((waited + stagger))
        done
      fi
      CUDA_VISIBLE_DEVICES="$i" \
      PORT="$((base + i))" \
      SHARD="$i/$gpus" \
      SKIP_DEPS="$([[ "$i" -gt 0 ]] && echo 1 || echo "${SKIP_DEPS:-0}")" \
      NO_TAR=1 \
      _FANOUT_CHILD=1 \
        bash "$self" ${_FANOUT_ARGS[@]+"${_FANOUT_ARGS[@]}"} > "$log" 2>&1
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
  rm -f .deps_ready
  echo ">> [$label] $n/$gpus shard(s) finished cleanly"
  # out/ is shared and the slices are disjoint, so there is nothing to merge —
  # package it once, from the parent, exactly as a single-GPU run would.
  [[ "$rc" == 0 ]] && kit_tar_out "$label"
  exit "$rc"
}

# Deps marker: child 0 calls this once its install has finished, releasing the
# others. A single-worker run calls it too and nobody is waiting — harmless.
deps_ready() { : > .deps_ready; }
