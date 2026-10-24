#!/usr/bin/env bash
# Runs any script in this folder in an ephemeral uv environment.
#   bash run.sh build_inputs.py                                   # step 1: dev opinions in the gold frame + sizing
#   bash run.sh make_requests.py --design d1                      # step 2: Clairic request bodies for one field design
#   bash run.sh run_clairic.py run --name d1 --requests data/requests/d1.jsonl --dry-run
#   bash run.sh to_predictions.py --run d1                        # step 4: answers -> pred_d1.json + usage report
#   python3 ../experiments_09022026/triage/analysis/score_encoder.py --pred data/pred_d1.json \
#       --ids $(cat ../experiments_09022026/triage/data/citation_seed/dev_ids.txt) --label "clairic d1"
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then set -a; . ./.env; set +a; fi   # CF_ACCOUNT_ID / CF_API_TOKEN (or CLOUDFLARE_* names)
exec uv run --no-project --with "tiktoken>=0.8" --with "httpx>=0.27" python "$@"
