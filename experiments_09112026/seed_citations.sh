#!/usr/bin/env bash
# GPT-5.6 citation-correction pass (the triage v8g fixer) over the 283 benchmark clusters that
# have no verified gold grouping, using the existing triage harness against a private
# annotator root (data/annotator_bench). Steps, in order:
#   bash seed_citations.sh root        # annotator root from data/opinions (CL html) for the 283
#   bash seed_citations.sh centralia   # replace CL html with centralia text where the coverage guard passed
#   bash seed_citations.sh prepare     # numbered inputs for the fixer  -> data/citation_seed/inputs
#   bash seed_citations.sh run         # GPT-5.6 Luna, prompt v8g + candidates, effort high (needs OPENAI_KEY)
#   bash seed_citations.sh check       # ok / errors / missing / non-stop finish reasons
#   bash seed_citations.sh redo <cid…> # re-run named clusters (e.g. a content_filter or truncated response)
#   bash seed_citations.sh apply       # write the edits into the root's grouping_overrides
#   bash seed_citations.sh export      # revised html for the 283 -> data/annotator_bench/data/revised_html
# OPENAI_KEY is read from the environment or from ./.env (gitignored).
set -euo pipefail
cd "$(dirname "$0")"
HERE=$PWD
TRIAGE=$HERE/../experiments_09022026/triage
ROOT=$HERE/data/annotator_bench
export CITSEED_ANNOT=$ROOT
export CITSEED_OUT=$HERE/data/citation_seed
[ -f .env ] && set -a && . ./.env && set +a
UVPY='uv run --no-project --with boto3>=1.34 --with json-repair>=0.25 --with requests>=2.31 --with eyecite python'
IDS=$(bash run.sh bench_ids.py seed | tr '\n' ' ')

case "${1:-}" in
  root)
    rm -rf data/bench_sample && mkdir -p data/bench_sample/opinion_html
    for c in $IDS; do cp "data/opinions/$c.json" data/bench_sample/opinion_html/; done
    python3 "$TRIAGE/make_annotator_data.py" --sample-dir data/bench_sample --out "$ROOT" --html-only
    $UVPY - <<'PY'
# real court / name / year for the root's citing_metadata.csv (html-only mode assumed scotus)
import csv, os
from common import DATA, load_citing_meta
meta = load_citing_meta()
p = os.path.join(DATA, "annotator_bench", "overrides", "citing_metadata.csv")
rows = list(csv.DictReader(open(p, encoding="utf-8")))
for r in rows:
    m = meta.get(r["cluster_id"], {})
    r["court"], r["case_name"], r["year"], r["source"] = m.get("court", r["court"]), m.get("name", ""), m.get("year", ""), "benchmark"
with open(p, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"citing_metadata.csv: {len(rows)} rows, courts filled from experiments_05182026")
PY
    ;;
  centralia)
    CIDS=$(bash run.sh bench_ids.py centralia | tr '\n' ' ')
    echo "feeding centralia text for $(echo $CIDS | wc -w | tr -d ' ') clusters"
    $UVPY "$TRIAGE/feed_centralia.py" --root "$ROOT" --centralia-out "$TRIAGE/data/centralia/out" --ids $CIDS
    ;;
  prepare)
    python3 "$TRIAGE/citation_seed.py" prepare --ids $IDS | tail -3
    ls "$CITSEED_OUT/inputs"/*.txt | wc -l
    ;;
  run)
    [ -n "${OPENAI_KEY:-}" ] || { echo "OPENAI_KEY not set: export it or put OPENAI_KEY=... in $HERE/.env" >&2; exit 2; }
    bash "$TRIAGE/openai_batch.sh" run --name bench_gpt --model gpt-5.6-luna \
      --prompt prompts/triage_citation_fixer_v8g.md --effort high --candidates --workers "${WORKERS:-8}" --ids $IDS
    ;;
  redo)
    shift; [ $# -gt 0 ] || { echo "usage: bash seed_citations.sh redo <cid> [cid …]" >&2; exit 2; }
    [ -n "${OPENAI_KEY:-}" ] || { echo "OPENAI_KEY not set: export it or put OPENAI_KEY=... in $HERE/.env" >&2; exit 2; }
    bash "$TRIAGE/openai_batch.sh" run --name bench_gpt --model gpt-5.6-luna \
      --prompt prompts/triage_citation_fixer_v8g.md --effort high --candidates --workers 2 --redo --ids "$@"
    ;;
  check)
    $UVPY - <<'PY'
import json, os, glob, collections
from common import DATA
S = os.path.join(DATA, "citation_seed"); J = json.load(open(f"{S}/bedrock_jobs.json"))["bench_gpt"]
u, e = J.get("usage", {}), J.get("errors", {})
missing = [c for c in J["ids"] if not os.path.exists(f"{S}/bench_gpt/{c}.edits.json")]
tin = sum(v.get("in") or 0 for v in u.values()); tout = sum(v.get("out") or 0 for v in u.values())
print(f"{len(u)} ok, {len(e)} errors, {len(missing)} without edits.json; finish reasons "
      f"{dict(collections.Counter(v.get('finish') for v in u.values()))}; ${tin/1e6*0.20 + tout/1e6*1.20:.2f} so far")
for c in missing: print("  missing:", c, e.get(c, "")[:100])
for c, v in u.items():
    if v.get("finish") != "stop": print("  non-stop finish:", c, v.get("finish"))
PY
    ;;
  apply)
    python3 "$TRIAGE/citation_seed.py" apply --write --ids $IDS --out-dir "$CITSEED_OUT/bench_gpt" | grep -c 'applied' 
    ;;
  export)
    ( cd /Users/rachel/Desktop/flp/citator-benchmark && \
      CITATOR_BENCH_DATA_DIR="$ROOT/data" CITATOR_BENCH_OVERRIDES_DIR="$ROOT/overrides" \
      uv run python "$HERE/export_revised_html.py" --ids $IDS )
    ;;
  *) echo "usage: bash seed_citations.sh root|centralia|prepare|run|check|redo|apply|export" >&2; exit 2 ;;
esac
