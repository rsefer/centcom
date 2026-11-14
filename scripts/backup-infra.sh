#!/bin/sh

# Deploys the CENTCOM backup infrastructure (S3 bucket, lifecycle expiry, and
# IAM sync policy) via CloudFormation. Idempotent: re-run to update.
#
# Run this with ADMIN AWS credentials (not the limited .env Route53 user),
# e.g. from your workstation:
#   AWS_PROFILE=admin ./scripts/backup-infra.sh my-backup-bucket route53-user-name [expiration-days]
#
# Uses the local aws CLI when available, otherwise falls back to the
# amazon/aws-cli Docker image with your ~/.aws config mounted read-only.

set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
	echo "Usage: $0 <bucket-name> <iam-user-name> [expiration-days]"
	exit 1
fi

BUCKET_NAME="$1"
IAM_USER="$2"
EXPIRATION_DAYS="${3:-45}"
STACK_NAME="centcom-backups"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

if command -v aws >/dev/null 2>&1; then
	aws cloudformation deploy \
		--stack-name "$STACK_NAME" \
		--template-file "$SCRIPT_DIR/backup-infra.yaml" \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides \
			"BucketName=$BUCKET_NAME" \
			"BackupUserName=$IAM_USER" \
			"ExpirationDays=$EXPIRATION_DAYS"
elif command -v docker >/dev/null 2>&1; then
	docker run --rm \
		-v "$HOME/.aws":/root/.aws:ro \
		-v "$SCRIPT_DIR":/infra:ro \
		-e AWS_PROFILE="${AWS_PROFILE:-}" \
		-e AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-}" \
		-e AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-}" \
		-e AWS_SESSION_TOKEN="${AWS_SESSION_TOKEN:-}" \
		-e AWS_REGION="${AWS_REGION:-us-east-1}" \
		amazon/aws-cli cloudformation deploy \
		--stack-name "$STACK_NAME" \
		--template-file /infra/backup-infra.yaml \
		--capabilities CAPABILITY_IAM \
		--parameter-overrides \
			"BucketName=$BUCKET_NAME" \
			"BackupUserName=$IAM_USER" \
			"ExpirationDays=$EXPIRATION_DAYS"
else
	echo "Neither the aws CLI nor docker is available"
	exit 1
fi

echo "Done. Set BACKUP_S3_BUCKET=$BUCKET_NAME in .env on the Pi."
