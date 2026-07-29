#!/usr/bin/env bash
# Build a self-contained pod kit: kit CODE + the shared modules it needs + the
# staged DATA for one set, as dist/<engine>_<set>_kit.tar.gz.
#
# Runs LOCALLY and never ships to a pod (fanout.sh, beside it, is the opposite:
# pod-side, copied into every kit here).
#
#   bash runpod/pack.sh <engine> <set>
#   bash runpod/pack.sh surya 1k
#
# engines: container_yolo | dots | surya | lighton
# sets:    whatever has a data/sets/<set>/set.conf
#
# A set's inputs are copied INTO the tarball, so the pod needs no credentials
# and no network beyond the model download: page IMAGES for the OCR/layout kits,
# region crops for lighton.
#
# A kit takes IMAGES, never PDFs: rendering belongs to the pipeline's render
# stage, which owns the canonical 1700x2200 space every bbox in this project
# lives in. So a set names where its page images are, and there is exactly one
# renderer in the codebase rather than a second one here that could drift and
# hand the pod different pixels than the pipeline reads.
#
#   set.conf:  DATASET=1k          # use data/datasets/1k/page_png (whole set)
#              IMAGES=images       # or a directory of PNGs, relative to the set
#              PAGES=pages.txt     # optional: one page id per line, to subset
#
# The repo holds code only. Everything under data/ is gitignored and staged
# here at pack time, so a kit in git never carries pages, crops, or weights.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="${1:-}"; SET="${2:-}"
[[ -n "$ENGINE" && -n "$SET" ]] || { sed -n '2,12p' "$0" >&2; exit 1; }

KITSRC="$HERE/kits/$ENGINE"
CONF="$HERE/data/sets/$SET/set.conf"
[[ -d "$KITSRC" ]] || { echo "!! no such engine: $ENGINE" >&2; exit 1; }
[[ -f "$CONF" ]] || { echo "!! no such set: $SET (expected $CONF)" >&2; exit 1; }
# shellcheck disable=SC1090
source "$CONF"
SETDIR="$HERE/data/sets/$SET"

STAGE="$HERE/.pack_stage"
KIT="$STAGE/$ENGINE"
rm -rf "$STAGE"; mkdir -p "$KIT"
trap 'rm -rf "$STAGE"' EXIT

echo ">> [$ENGINE/$SET] staging code"
cp "$KITSRC"/*.py "$KITSRC"/*.sh "$KITSRC"/README.md "$KIT"/
# Multi-GPU fan-out, shared by every kit: run.sh sources it and honours GPUS=n.
cp "$HERE/fanout.sh" "$KIT/"
cp "$HERE/README.md" "$KIT/PACKAGE_README.md"   # the kit contract, for context
printf '%s\n' "$SET" > "$KIT/SET"

if [[ "$ENGINE" == lighton ]]; then
  # LightOn reads disputed-region CROPS, not pages — a different data contract.
  CROPS="$SETDIR/crops"
  [[ -d "$CROPS" && -s "$SETDIR/manifest.jsonl" ]] || {
    echo "!! $ENGINE needs $CROPS + $SETDIR/manifest.jsonl (built locally from the" >&2
    echo "!! pipeline's disputes — see kits/lighton/README.md)" >&2; exit 1; }
  mkdir -p "$KIT/crops"
  cp "$CROPS"/*.png "$KIT/crops/"
  cp "$SETDIR/manifest.jsonl" "$KIT/"
  echo ">> [$ENGINE/$SET] $(find "$KIT/crops" -name '*.png' | wc -l | tr -d ' ') crops staged"
else
  # Where this set's page images are: a dataset's render output, or a directory
  # of PNGs relative to the set (absolute paths pass through).
  if [[ -n "${DATASET:-}" ]]; then
    IMAGES="$HERE/../data/datasets/$DATASET/page_png"
  else
    IMAGES="${IMAGES:-images}"
    case "$IMAGES" in /*) ;; *) IMAGES="$SETDIR/${IMAGES#./}" ;; esac
  fi
  compgen -G "$IMAGES/*.png" > /dev/null || {
    echo "!! no page images at $IMAGES" >&2
    echo "!! set.conf needs DATASET=<name> (uses that dataset's page_png) or" >&2
    echo "!! IMAGES=<dir>. Images come from the pipeline's render stage —" >&2
    echo "!! build the dataset first, this does not render." >&2
    exit 1; }
  mkdir -p "$KIT/images"
  # PAGES, when set, is a file of page ids (one per line) selecting a subset —
  # a 10-page smoke run off a 1,000-page dataset needs no separate copy of it.
  if [[ -n "${PAGES:-}" ]]; then
    case "$PAGES" in /*) ;; *) PAGES="$SETDIR/${PAGES#./}" ;; esac
    [[ -s "$PAGES" ]] || { echo "!! page list missing: $PAGES" >&2; exit 1; }
    while IFS= read -r stem; do
      [[ -n "$stem" ]] || continue
      cp "$IMAGES/$stem.png" "$KIT/images/" 2>/dev/null || {
        echo "!! no image for page id '$stem' in $IMAGES" >&2; exit 1; }
    done < "$PAGES"
  else
    # One cp for the whole glob — thousands of pages make a per-file -exec
    # painfully slow, and the leading-dot glob skips AppleDouble ._* for free.
    cp "$IMAGES"/*.png "$KIT/images/"
  fi
  echo ">> [$ENGINE/$SET] $(find "$KIT/images" -name '*.png' | wc -l | tr -d ' ') page images staged"
fi

# macOS hygiene. Every local file carries xattrs (com.apple.provenance, and
# com.apple.quarantine on anything downloaded), which is what turns into
# AppleDouble `._*` members inside a tarball. Those would land on the pod as
# junk siblings that every `find`/glob then has to filter. Belt AND braces:
# COPYFILE_DISABLE, the bsdtar flags where supported, explicit excludes — and
# then an actual inspection of the result below, because the guarantee should
# not depend on which tar happens to be on PATH.
TAR_OPTS=(--exclude '.DS_Store' --exclude '._*' --exclude '__MACOSX'
          --exclude '__pycache__' --exclude '*.part')
if tar --help 2>&1 | grep -q -- '--no-mac-metadata'; then
  TAR_OPTS+=(--no-mac-metadata --no-xattrs)
fi

mkdir -p "$HERE/dist"
TAR="$HERE/dist/${ENGINE}_${SET}_kit.tar.gz"
rm -f "$TAR"
COPYFILE_DISABLE=1 tar czf "$TAR" -C "$STAGE" "${TAR_OPTS[@]}" "$ENGINE"

# Verify with PYTHON, not `tar tzf`. bsdtar silently CONSUMES `._*` members on
# read — it treats them as AppleDouble metadata for their sibling and never
# lists them — so `tar tzf | grep` reports a clean tarball even when the junk
# is right there. GNU tar on the pod would then happily unpack them. Python's
# tarfile lists members verbatim, which is the only honest check here.
BAD="$(python3 - "$TAR" <<'PY'
import re, sys, tarfile
pat = re.compile(r"(^|/)(\._|\.DS_Store$|__MACOSX(/|$))")
with tarfile.open(sys.argv[1]) as t:
    for n in t.getnames():
        if pat.search(n):
            print(n)
PY
)"
if [[ -n "$BAD" ]]; then
  echo "!! macOS artifacts made it into $TAR — the pod would need cleanup:" >&2
  echo "$BAD" | head -20 >&2
  rm -f "$TAR"
  exit 1
fi
echo ">> $TAR ($(du -h "$TAR" | cut -f1)) — verified free of macOS artifacts"
echo ">> transfer:  runpodctl send $TAR"
