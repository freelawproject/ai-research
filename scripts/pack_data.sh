#!/bin/sh
# Build the shareable data zip from the repo's data/ folder.
# Run from anywhere:  sh scripts/pack_data.sh
# Writes extraction_data_<date>.zip one level above the repo root.
set -e

cd "$(dirname "$0")/.."

if [ ! -d data ]; then
    echo "error: no data/ folder at the repo root" >&2
    exit 1
fi

out="../extraction_data_$(date +%Y%m%d).zip"
rm -f "$out"
# exports/ is pod-kit staging (manage.py export_crops), never shipped
zip -rq "$out" data -x '*.DS_Store' -x 'data/exports/*'
echo "wrote $(cd .. && pwd)/$(basename "$out")"
