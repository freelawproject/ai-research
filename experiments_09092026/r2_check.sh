#!/usr/bin/env bash
# Round-2 GPT seeding checker: refresh the four OpenAI Batch parts, fetch any
# part that has completed but is not fetched yet, and apply every fetched
# opinion (edits.json present, not yet applied) into data/annotator_r2.
# Idempotent; safe to run from a cron every 30 min. Log: data/citation_seed_r2/checker.log
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TRIAGE="$HERE/../experiments_09022026/triage"
export CITSEED_ANNOT="$HERE/data/annotator_r2" CITSEED_OUT="$HERE/data/citation_seed_r2"
export OPENAI_KEY="${OPENAI_KEY:-$(conda run -n citator printenv OPENAI_KEY | tr -d '\r\n')}"
LOG="$CITSEED_OUT/checker.log"
cd "$TRIAGE"
{
echo "=== $(date '+%F %T') ==="
bash openai_batch.sh status 2>/dev/null | grep '^r2_gpt'
for part in $(python3 -c "import json;d=json.load(open('$CITSEED_OUT/bedrock_jobs.json'));print(' '.join(n for n,m in d.items() if m.get('status')=='completed' and not m.get('fetched')))"); do
  echo "--- fetch $part"
  bash openai_batch.sh fetch --name "$part" 2>/dev/null | grep -v Warning | tail -4
done
# apply everything fetched but not yet applied
TODO=$(python3 - <<'PY'
import json, os
out=os.environ['CITSEED_OUT']; ann=os.environ['CITSEED_ANNOT']
d=json.load(open(f'{out}/bedrock_jobs.json'))
ids=[]
for m in d.values():
    if not m.get('fetched'): continue
    for cid in m['ids']:
        if not os.path.exists(f'{out}/r2_gpt/{cid}.edits.json'): continue
        p=f'{ann}/data/grouping_overrides/{cid}.json'
        ov=json.load(open(p)) if os.path.exists(p) else {}
        if not ov.get('llm_citation_pass'): ids.append(cid)
print(' '.join(ids))
PY
)
if [ -n "$TODO" ]; then
  echo "--- apply $(echo $TODO | wc -w | tr -d ' ') opinions"
  python3 citation_seed.py apply --write --out-dir "$CITSEED_OUT/r2_gpt" --ids $TODO 2>&1 | grep -c "written to overrides" | sed 's/^/written: /'
fi
python3 -c "
import json,os,glob
ann=os.environ['CITSEED_ANNOT']
n=sum(1 for p in glob.glob(f'{ann}/data/grouping_overrides/*.json') if json.load(open(p)).get('llm_citation_pass'))
print(f'applied total: {n} / 10000')"
} >> "$LOG" 2>&1
tail -12 "$LOG"
