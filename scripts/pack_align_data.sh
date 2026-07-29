#!/bin/sh
# Build the shareable ALIGNMENT data zip from the repo's align_data/ folder.
# Run from anywhere:  sh scripts/pack_align_data.sh
# Writes alignment_data_<date>.zip one level above the repo root.
#
# This bundle feeds the alignment surface (/align/) only. The route
# surface's bundle is built by pack_data.sh — the two roots are separate
# precisely so each zip is unambiguous about which viewer it feeds.
set -e

cd "$(dirname "$0")/.."

if [ ! -d align_data ]; then
    echo "error: no align_data/ folder at the repo root" >&2
    exit 1
fi

out="../alignment_data_$(date +%Y%m%d).zip"
rm -f "$out"
# A staged set may symlink its trees from elsewhere; zip follows the
# links and stores real files, so the bundle stands alone.
# source/ (whole-volume PDFs) stays out: provenance only — discovery
# reads page_png/, nothing ever reopens a volume, and check_data treats
# the folder as optional. ._* and .DS_Store are macOS sidecars.
zip -rq "$out" align_data \
    -x '*.DS_Store' \
    -x '*/._*' \
    -x 'align_data/datasets/*/source/*'
echo "wrote $(cd .. && pwd)/$(basename "$out") ($(du -h "$out" | cut -f1))"
