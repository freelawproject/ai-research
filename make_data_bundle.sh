#!/usr/bin/env bash
# Build the data bundle for the delivery viewer — RUN ON THE ENCODER
# MACHINE (needs encoder/dataset + infer_out). The bundle carries gold
# labels, predictions, and redacted page scans; it is shared with the
# team out-of-band and never committed.
#
#   bash make_data_bundle.sh /path/to/flp/ai-research-encoder/encoder
#   → blocktagger_data_<date>.zip
set -euo pipefail
cd "$(dirname "$0")"

ENC="${1:?usage: make_data_bundle.sh /path/to/ai-research-encoder/encoder}"
RUN="blocktagger_large_bigset"

rm -rf data
mkdir -p data/preds/{bigset_val,bigset_test} data/redacted

cp "$ENC/dataset/data/bigset_val.jsonl"  data/
cp "$ENC/dataset/data/bigset_test.jsonl" data/
cp "$ENC/train/infer_out/$RUN/bigset_val/pred.jsonl"  data/preds/bigset_val/
cp "$ENC/train/infer_out/$RUN/bigset_test/pred.jsonl" data/preds/bigset_test/

# pages.json: window→page mapping + the redacted scans for exactly the
# val/test ranges
python3 - "$ENC" <<'PYEOF'
import json, shutil, sys
from pathlib import Path

enc = Path(sys.argv[1])
rngs = set()
for f in ("bigset_val.jsonl", "bigset_test.jsonl"):
    for line in open(f"data/{f}"):
        rngs.add(json.loads(line)["doc"])
pages = {}
n_img = 0
for rng in sorted(rngs):
    doc = json.loads((enc / "dataset/data_seeded" / f"{rng}.json")
                     .read_text())
    pages[rng] = [{"stem": p["stem"], "start": p["start"],
                   "end": p["end"]} for p in doc["pages"]]
    for p in doc["pages"]:
        src = enc / "dataset/big5k/redacted" / f"{rng}__{p['stem']}.png"
        if src.exists():
            shutil.copy(src, f"data/redacted/{rng}__{p['stem']}.png")
            n_img += 1
json.dump(pages, open("data/pages.json", "w"))
print(f"{len(rngs)} ranges, {sum(len(v) for v in pages.values())} pages, "
      f"{n_img} redacted scans")
PYEOF

OUT="blocktagger_data_$(date +%Y%m%d).zip"
zip -qr "$OUT" data
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
