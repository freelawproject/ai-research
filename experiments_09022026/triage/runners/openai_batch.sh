#!/usr/bin/env bash
# Wrapper for openai_batch.py: needs $OPENAI_KEY (same variable as
# experiments_04012026/utils/gpt_utils.py); export is offline.
#   bash openai_batch.sh models
#   bash openai_batch.sh export --name dev_gpt --ids ... --model <id>
#   bash openai_batch.sh submit|status|wait|fetch --name dev_gpt
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-dev-env}
export AWS_REGION=${AWS_REGION:-us-west-2}
# `run --provider bedrock` goes through Bedrock Converse on the SSO session; no OpenAI key involved
if [ "${1:-}" != "export" ] && [ -z "${OPENAI_KEY:-}" ] && ! printf '%s\n' "$@" | grep -qx -- bedrock; then
  echo "export OPENAI_KEY=... first (the OpenAI API key, as in experiments_04012026/utils/gpt_utils.py)" >&2
  exit 2
fi
exec uv run --no-project --with "openai>=1.60,<2" --with "boto3>=1.34" --with "json-repair>=0.25" --with "pandas>=2.0" python openai_batch.py "$@"
