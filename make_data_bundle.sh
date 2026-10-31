#!/usr/bin/env bash
# Build the eval-data bundle for this package — RUN ON THE ENCODER
# MACHINE. By default the bundle carries the portal set only
# (`portal_eval.jsonl`: the opinions of scanning issue #458 in the
# portal's own input shape, with reviewed gold), which is the set the
# scanning team needs, plus the previous model's predictions on it
# (`before/portal_eval/pred.jsonl`, the viewer's left pane: the shipped
# weights with the per-token decode, as deployed). SETS="…" picks other
# val files; the test sets never leave that machine and the script
# refuses them. Share the archive out-of-band and never commit it.
#
#   bash make_data_bundle.sh /path/to/ai-research-encoder/encoder
#   SETS="val.jsonl bigset_val.jsonl portal_eval.jsonl" bash make_data_bundle.sh …
#   → tagger_eval_data_<date>.tar.gz  (unpack here → data/, before/)
set -euo pipefail
cd "$(dirname "$0")"
ENC="${1:?usage: make_data_bundle.sh /path/to/ai-research-encoder/encoder}"
SETS="${SETS:-portal_eval.jsonl}"
PREV="$ENC/train/infer_out/blocktagger_large_bigset"   # the previous model, per-token decode

STAGE="$(mktemp -d)"; trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/data"
for f in $SETS; do
  case "$f" in *test*) echo "$f: test files never leave the encoder machine — refusing" >&2; exit 1 ;; esac
  cp "$ENC/dataset/data/$f" "$STAGE/data/"
done
for f in $SETS; do
  ds="${f%.jsonl}"
  if [ -f "$PREV/$ds/pred.jsonl" ]; then
    mkdir -p "$STAGE/before/$ds"
    cp "$PREV/$ds/pred.jsonl" "$PREV/$ds/eval_spans.json" "$STAGE/before/$ds/"
  else
    echo "note: no previous-model predictions for $ds ($PREV/$ds) — before pane will be empty" >&2
  fi
done
OUT="$PWD/tagger_eval_data_$(date +%Y%m%d).tar.gz"
tar -C "$STAGE" -czf "$OUT" data before
echo "wrote $OUT ($(du -h "$OUT" | cut -f1)):"; (cd "$STAGE" && find data before -type f | sort)
