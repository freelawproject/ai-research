#!/usr/bin/env bash
# Runs any script in this folder in an ephemeral uv environment.
#   bash run.sh opinion_length.py                     # step 1: opinion length vs each model's context window
#   bash run.sh build_contexts.py                     # step 2: setup A (mention) + B (concat) inputs
#   bash run.sh make_requests.py --experiment label --model jev-1.14.0   # step 3: one-shot labeler request bodies per setup
#   bash run.sh run_systemone.py run --name jev_label_mention --requests data/requests/jev-1.14.0_label_mention.jsonl --dry-run
#   bash run.sh score.py --run jev_label_mention --run jev_label_concat
set -euo pipefail
cd "$(dirname "$0")"
exec uv run --no-project --with "tiktoken>=0.8" --with "transformers>=4.45" --with "httpx>=0.27" python "$@"
