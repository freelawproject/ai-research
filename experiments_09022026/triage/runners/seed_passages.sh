#!/usr/bin/env bash
# Wrapper for seed_passages.py (passage-level treatment seeding on Bedrock batch).
# prepare/apply/score are offline (prepare needs the annotator viewer on :8125);
# submit/status/wait/fetch need the dev-env SSO session + CITATOR_S3_BUCKET / CITATOR_BATCH_ROLE_ARN.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PROFILE=${AWS_PROFILE:-dev-env}
export AWS_REGION=${AWS_REGION:-us-west-2}
case "${1:-}" in
  submit|status|wait|fetch)
    if ! aws sts get-caller-identity --profile "$AWS_PROFILE" >/dev/null 2>&1; then
      echo "SSO session for profile $AWS_PROFILE is not valid. Run:  aws sso login --profile $AWS_PROFILE" >&2; exit 2
    fi
    if [ -z "${CITATOR_S3_BUCKET:-}" ] || [ -z "${CITATOR_BATCH_ROLE_ARN:-}" ]; then
      echo "export CITATOR_S3_BUCKET and CITATOR_BATCH_ROLE_ARN first" >&2; exit 2
    fi;;
esac
exec uv run --no-project --with "boto3>=1.34" --with "json-repair>=0.25" --with "pandas>=2.0" python seed_passages.py "$@"
