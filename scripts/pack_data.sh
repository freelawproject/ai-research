#!/bin/sh
# Build the shareable data zip from the repo's data/ folder.
# Run from anywhere:  sh scripts/pack_data.sh
# Writes extraction_data_<date>.zip one level above the repo root.
#
# The bundle carries what the viewer can actually show: every dataset's pages
# and cached engine outputs, the layout weights, and the artifacts for the
# combinations each dataset PRESENTS.
set -e

cd "$(dirname "$0")/.."

if [ ! -d data ]; then
    echo "error: no data/ folder at the repo root" >&2
    exit 1
fi

# A combination a dataset does not present is unreachable in the viewer (the
# route 404s, and it is absent from the stats table and every picker), and its
# artifacts go stale the moment the registry moves — so shipping them would
# hand over numbers nothing can display. The list is derived from
# pipeline.routes rather than hardcoded, so it follows VOTE_ONLY_DATASETS
# instead of drifting away from it.
withheld="$(uv run python - <<'PY'
from pathlib import Path

from pipeline import routes

root = Path("data/artifacts")
sets = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
for dataset in sets:
    for name in sorted(p.name for p in (root / dataset).iterdir() if p.is_dir()):
        try:
            routes.resolve(name)
        except ValueError:
            continue  # not a combination at all — leave whatever it is alone
        if not routes.presented(dataset, name):
            print(f"data/artifacts/{dataset}/{name}/*")
PY
)"
if [ -n "$withheld" ]; then
    echo "$withheld" | sed 's|/\*$||; s|^|  withheld, not shipped: |'
fi

out="../extraction_data_$(date +%Y%m%d).zip"
rm -f "$out"
# set -f: the exclude patterns are zip's to interpret, not the shell's to
# expand — unquoted, a `*` pattern would otherwise become thousands of paths.
set -f
# exports/ is pod-kit staging (manage.py export_crops), never shipped.
# surya/line/ is cached inference no combination reads: the only surya slug is
# surya_block, so nothing ever asks for the line variant.
# ._* are macOS AppleDouble sidecars left by unpacking a kit's output tarball
# here. Nothing reads them (the caches are read by exact path), but check_data
# counts files per directory, so shipping them reports inflated cache sizes.
# shellcheck disable=SC2086  # word splitting is how the pattern list is passed
zip -rq "$out" data \
    -x '*.DS_Store' \
    -x '*/._*' \
    -x 'data/exports/*' \
    -x 'data/datasets/*/engines/surya/line/*' \
    $withheld
set +f
echo "wrote $(cd .. && pwd)/$(basename "$out") ($(du -h "$out" | cut -f1))"
