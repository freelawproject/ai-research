#!/usr/bin/env bash
# Runs any script in this folder in an ephemeral uv environment with the deps the
# citator-pipeline batch utilities need. AWS calls use the dev-env SSO profile.
#   bash run.sh build_inputs.py fetch
#   bash run.sh run_stage.py prepare --stage gate --name g_kimi_dev --model kimi --split dev
#   bash run.sh score.py --gate g_kimi_dev --label l_sonnet_dev
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-dev-env}
export AWS_REGION=${AWS_REGION:-us-west-2}
exec uv run --no-project --with "boto3>=1.34" --with "json-repair>=0.25" --with "requests>=2.31" --with "eyecite" python "$@"
