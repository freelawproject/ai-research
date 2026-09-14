#!/usr/bin/env bash
# Wrapper for bedrock_batch.py: checks the dev-env SSO session and the batch env
# vars the citator-pipeline batch runs use, then runs the script in an ephemeral
# uv env with the same deps as experiments_06172026 (boto3, json-repair, pandas).
#   bash bedrock_batch.sh models
#   bash bedrock_batch.sh export --name train_a --ids ...       # offline
#   bash bedrock_batch.sh submit --name train_a [--model <id>]  # needs CITSEED_MODEL_ID or --model
#   bash bedrock_batch.sh status | wait --name train_a | fetch --name train_a
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-dev-env}
export AWS_REGION=${AWS_REGION:-us-west-2}

if [ "${1:-}" != "export" ]; then  # export is offline; everything else needs credentials
  if ! aws sts get-caller-identity --profile "$AWS_PROFILE" >/dev/null 2>&1; then
    echo "SSO session for profile $AWS_PROFILE is not valid. Run:  aws sso login --profile $AWS_PROFILE" >&2
    exit 2
  fi
  if [ "${1:-}" != "models" ] && { [ -z "${CITATOR_S3_BUCKET:-}" ] || [ -z "${CITATOR_BATCH_ROLE_ARN:-}" ]; }; then
    echo "export CITATOR_S3_BUCKET and CITATOR_BATCH_ROLE_ARN first (same values as the citator-pipeline batch runs)" >&2
    exit 2
  fi
fi
exec uv run --no-project --with "boto3>=1.34" --with "json-repair>=0.25" --with "pandas>=2.0" python bedrock_batch.py "$@"
