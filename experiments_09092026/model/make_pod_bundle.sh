#!/usr/bin/env bash
# Self-contained pod bundle: model code + the built dataset.
#
#   bash model/make_pod_bundle.sh          # silver: silver_pod_bundle.tar.gz (no weights; pod pulls the HF backbone)
#   R2=1 bash model/make_pod_bundle.sh     # round 2: r2_pod_bundle.tar.gz = code + citations_r2/mention_features_r2
#                                             #   (staged under the canonical names) + init/ = the silver checkpoints
#                                             #   (~3 GB) for MODE=warm; MODE=cold uses the same bundle without them
#   R3=1 bash model/make_pod_bundle.sh     # round 3: r3_pod_bundle.tar.gz = same round-2 data + the silver LINKER
#                                             #   checkpoint only (Task B is the only thing retrained)
#   R5=1 bash model/make_pod_bundle.sh     # round 5: r5_pod_bundle.tar.gz = round-2 records + mention_features_r2e
#                                             #   (with eyecite_gid) + the ROUND-4 linker checkpoint as the warm start
set -euo pipefail
cd "$(dirname "$0")"
FT="$PWD"; EXP="$(dirname "$FT")"
FEATS_SFX=""
if [ "${R5:-0}" = 1 ]; then
  TAG=r5; SFX=_r2; FEATS_SFX=_r2e; OUT="$EXP/r5_pod_bundle.tar.gz"   # round 5 = round-2 data + eyecite column
elif [ "${R3:-0}" = 1 ]; then
  TAG=r3; SFX=_r2; OUT="$EXP/r3_pod_bundle.tar.gz"   # round 3 retrains on the round-2 data
elif [ "${R2:-0}" = 1 ]; then
  TAG=r2; SFX=_r2; OUT="$EXP/r2_pod_bundle.tar.gz"
else
  TAG=silver; SFX=; OUT="$EXP/silver_pod_bundle.tar.gz"
fi
FEATS_SFX=${FEATS_SFX:-$SFX}
for f in "citations$SFX.jsonl" "mention_features$FEATS_SFX.jsonl"; do
  [ -f "$EXP/data/output/$f" ] || { echo "ERROR: missing data/output/$f — run build_dataset.py + mention_features.py" >&2; exit 1; }
done
STAGE="$(mktemp -d)/${TAG}_pod"
mkdir -p "$STAGE/model" "$STAGE/data/output"
for f in *.py *.sh *.toml; do [ -e "$f" ] && cp "$f" "$STAGE/model/"; done
# the trainers read the canonical names (common.py: ../data/output/citations.jsonl)
cp "$EXP/data/output/citations$SFX.jsonl" "$STAGE/data/output/citations.jsonl"
cp "$EXP/data/output/mention_features$FEATS_SFX.jsonl" "$STAGE/data/output/mention_features.jsonl"
if [ "$TAG" = r2 ] || [ "$TAG" = r3 ] || [ "$TAG" = r5 ]; then
  # rounds 3 and 5 are Task B only, so they need one linker checkpoint and nothing else
  INITS="extract_large-caselaw link_large-caselaw"
  [ "$TAG" = r3 ] && INITS="link_large-caselaw"
  [ "$TAG" = r5 ] && INITS="link_large-caselaw_r4"
  for r in $INITS; do
    [ -d "$EXP/runs/$r/best" ] || { echo "ERROR: missing runs/$r/best (silver checkpoints for the warm start)" >&2; exit 1; }
    mkdir -p "$STAGE/init/$r"
    cp -R "$EXP/runs/$r/best" "$STAGE/init/$r/best"
  done
  rm -f "$STAGE"/init/*/best/coref_*.json "$STAGE"/init/*/best/training_args.bin
  if [ "$TAG" = r5 ]; then
  cat > "$STAGE/RUN.md" <<'MD'
# round 5 — linker with eyecite's grouping as an input channel — pod bundle

    model/               code (common.py resolves ../data/output/*)
    data/output/         the SAME records round 2-4 trained on; mention_features.jsonl now carries
                         eyecite_gid (eyecite's own group per span, null where eyecite tagged nothing)
    init/                the round-4 linker = the warm start (pair scorer padded with 3 zero columns)

    cd model
    MODE=r5link bash run_runpod.sh              # + eyecite channel, 20% case dropout -> runs/link_large-caselaw_r5
    EYE=0 MODE=r5link bash run_runpod.sh        # control: same warm start, data, epochs, no channel -> *_r5ctl
    MAX_TRAIN=200 MODE=r5link bash run_runpod.sh   # memory pilot first (minutes)
    runpodctl send r5_link_runs_r5.tar.gz; runpodctl send r5_link_runs_r5ctl.tar.gz

Extraction is NOT retrained. Everything else (window 128/128, 700-mention cap,
chunk recompute, lr 1e-5, 2 epochs) is identical to MODE=r4link; the ONLY
variable between _r5 and _r5ctl is the eyecite channel. best/coref_test.json =
gold_dev with the channel, best/coref_test_noeye.json = the same model with the
channel masked (eyecite not run in front of it).

Score on the laptop: RUN_SUFFIX=_warm LINK_SUFFIX=_r5 inference.py --eyecite-feats
(add --no-eyecite-input for the masked condition) -> score_encoder.py.
MD
  elif [ "$TAG" = r3 ]; then
  cat > "$STAGE/RUN.md" <<'MD'
# round 3 — linker rebuild (chunked, wider window, no-antecedent mentions) — pod bundle

    model/               code (common.py resolves ../data/output/*)
    data/output/         the SAME dataset round 2 trained on (train / validation / train_high_quality / gold_dev / gold_test)
    init/                silver linker checkpoint = the warm-start weights (identical to MODE=warm's)

    cd model
    MODE=r4link bash run_runpod.sh        # Task B only, lwin 64->128 + chunk recompute -> runs/link_large-caselaw_r4
    MAX_TRAIN=200 MODE=r4link bash run_runpod.sh   # memory pilot first (minutes)
    runpodctl send r4_link_runs.tar.gz
    # MODE=r3link = the injected-dummies run (negative result: id attach 0.894 -> 0.808)

Extraction is NOT retrained — extract_large-caselaw_warm stays authoritative, and
the window (ctx 128 / lwin 64), data, warm start and mention cap (700) are all
identical to MODE=warm. The ONLY variable is the injected no-antecedent mentions:
untagged Id./Ibid./supra are added with a cluster id of their own so "refuse to
link" becomes a trained answer (48,042 of them, 6.3% of train mentions). Phantoms
are excluded from B3 / attach and never reach the output.

Compare against link_large-caselaw_warm: B3 F1 0.969, id attach 0.894 — id attach
is the number this is aimed at.

Widening the window is deliberately NOT in this run: at lwin=256 the retained
activations are 4x and it OOMs at ~8% (chunking bounds the peak inside one
encoder call, but training retains every chunk until backward).

Score on the laptop as usual: RUN_SUFFIX=_r3 inference.py -> score_encoder.py
(its --ctx/--lwin defaults already match this run's 320/256).
MD
  else
  cat > "$STAGE/RUN.md" <<'MD'
# round 2 — GPT-seeded citation extraction + coref, warm vs cold start — pod bundle

    model/               code (common.py resolves ../data/output/*)
    data/output/         citations.jsonl: train / validation (9,994 GPT-seeded clusters, 2% val by hash)
                         + train_high_quality (261 Opus-corrected triage train clusters) + gold_dev / gold_test
    init/                silver checkpoints (round 1) = the warm-start weights

    cd model
    MODE=warm bash run_runpod.sh          # init from ../init, lr 1e-5, 2 epochs -> runs/*_warm, r2_warm_runs.tar.gz
    MODE=cold bash run_runpod.sh          # HF backbone, lr 3e-5/2e-5, 3 epochs -> runs/*_cold, r2_cold_runs.tar.gz
    MAX_TRAIN=2000 MODE=warm bash run_runpod.sh   # pilot
    runpodctl send r2_warm_runs.tar.gz    # back on the laptop: runpodctl receive <code>; then
                                          # SILVER_RUNS=<extracted runs dir> inference.py -> score_encoder.py

Selection is on validation (same label distribution as training); eval_test.json /
coref_test.json report gold_dev with the exact-span metric — the headline
(triage-scorer) numbers come from inference.py + score_encoder.py on the
laptop. Both modes can run on one 80GB card back to back (MODE=warm; MODE=cold).
The HF model is public; if the download needs a token: `export HF_TOKEN=...`.
MD
  fi
else
  cat > "$STAGE/RUN.md" <<'MD'
# eyecite-seeded citation extraction + coref — pod bundle

    model/               code (common.py resolves ../data/output/*)
    data/output/         citations.jsonl (train/val silver + gold_dev/gold_test) + mention_features.jsonl

    cd model
    bash run_runpod.sh                    # CaseLawModernBERT-large, extraction then linking
    MAX_TRAIN=2000 bash run_runpod.sh     # pilot on 2,000 clusters first
    runpodctl send silver_runs.tar.gz     # results: runs/*/eval_test.json = gold_dev scores

The HF model is public; if the download needs a token: `export HF_TOKEN=...`.
MD
fi
rm -f "$OUT"; tar czf "$OUT" -C "$(dirname "$STAGE")" "${TAG}_pod"; rm -rf "$(dirname "$STAGE")"
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"
case "$TAG" in
  r5) MODEMSG="MODE=r5link " ;;
  r3) MODEMSG="MODE=r3link " ;;
  r2) MODEMSG="MODE=warm " ;;
  *)  MODEMSG="" ;;
esac
echo "pod: tar xzf $(basename "$OUT") && cd ${TAG}_pod/model && ${MODEMSG}bash run_runpod.sh"
