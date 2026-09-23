#!/usr/bin/env bash
# Run a SECOND citator-benchmark viewer over the triage annotation root.
#   ./run_annotator.sh [annotator_root] [port]
# Defaults: data/annotator, 8125. Benchmark's own data is untouched: both roots
# are redirected via env vars the viewer reads at import.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TRIAGE="$(dirname "$HERE")"
ROOT="$(cd "${1:-$TRIAGE/data/annotator}" && pwd)"
PORT="${2:-8125}"
FLP="$(cd "$TRIAGE/../../.." && pwd)"          # the workspace holding both checkouts
BENCH="${CITATOR_BENCH:-$FLP/citator-benchmark}"
export CITATOR_BENCH_DATA_DIR="$ROOT/data"
export CITATOR_BENCH_OVERRIDES_DIR="$ROOT/overrides"
export CITATOR_BENCH_PASSAGES_DIR="$TRIAGE"   # enables /api/passages (model-input passages) on the grouping page
echo "annotator root: $ROOT  ->  http://127.0.0.1:$PORT/records?tab=citing"
cd "$BENCH" && exec uv run uvicorn app:app --host 127.0.0.1 --port "$PORT"
