#!/usr/bin/env bash
# Export GPT-seeded round-2 labels as revised HTML (see export_r2_revised_html.py).
#   bash corpus/export_r2_revised_html.sh [--limit N] [--force]
# Points the viewer code at data/annotator_r2 through the same env vars
# run_annotator.sh uses, and runs inside citator-benchmark's uv environment.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
EXP="$(dirname "$HERE")"                 # the experiment root; data/ lives there
ROOT="$EXP/data/annotator_r2"
BENCH="${CITATOR_BENCH:-$EXP/../../citator-benchmark}"   # the citator-benchmark checkout
export CITATOR_BENCH_DATA_DIR="$ROOT/data"
export CITATOR_BENCH_OVERRIDES_DIR="$ROOT/overrides"
export CITATOR_BENCH_PASSAGES_DIR="$EXP/../experiments_09022026/triage"
cd "$BENCH" && exec uv run python "$HERE/export_r2_revised_html.py" --root "$ROOT" "$@"
