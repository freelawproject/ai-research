#!/usr/bin/env bash
# Copy the evaluation data between this folder and S3. Data is never committed.
#
#   ./sync_data.sh push     # data/ -> S3
#   ./sync_data.sh pull     # S3 -> data/
#   DRYRUN=1 ./sync_data.sh push
#
# Needs an AWS session: aws sso login --profile dev-env
# Nothing is deleted on either side. data/backups/ (local safety copies of the
# label store) is left out.
set -euo pipefail

S3_PREFIX=${S3_PREFIX:-s3://flp-ai-private/Golden-DO-NOT-DELETE/case-law/case-name-eval}
AWS_PROFILE_NAME=${AWS_PROFILE_NAME:-dev-env}
HERE=$(cd "$(dirname "$0")" && pwd)

case "${1:-}" in
  push) src="$HERE/data/"; dst="$S3_PREFIX/" ;;
  pull) src="$S3_PREFIX/"; dst="$HERE/data/" ;;
  *) echo "usage: $0 push|pull" >&2; exit 2 ;;
esac

aws s3 sync "$src" "$dst" \
  --profile "$AWS_PROFILE_NAME" \
  --exclude "backups/*" --exclude "*.DS_Store" \
  ${DRYRUN:+--dryrun}
