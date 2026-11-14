#!/bin/sh

# Backs up all CENTCOM persistent data to a timestamped directory, prunes old
# backups, and optionally syncs the new backup to S3.
#
# Configuration (via .env or environment):
#   BACKUP_ROOT       Local backup destination (default: ~/centcom-backups)
#   BACKUP_RETENTION  Number of local backups to keep (default: 10)
#   BACKUP_S3_BUCKET  S3 bucket name; S3 sync is skipped when unset
#   BACKUP_S3_PREFIX  Key prefix inside the bucket (default: centcom)

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

if [ -f .env ]; then
	set -a
	. ./.env
	set +a
fi

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/centcom-backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-10}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"

failures=""

# Archives a mount (named volume or host path) using a throwaway container so
# no archive tooling or read permissions are needed on the host. Extra
# arguments are passed to tar (e.g. --exclude patterns).
archive_mount() {
	name="$1"
	source="$2"
	shift 2

	echo "Backing up $name"
	if docker run --rm -v "$source":/source:ro -v "$BACKUP_DIR":/backup alpine \
		tar czf "/backup/$name.tar.gz" -C /source "$@" .; then
		return 0
	fi

	echo "✗ Failed to back up $name"
	failures="$failures $name"
	return 0
}

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_ROOT" "$BACKUP_DIR"

# openGym: stop the API so the sqlite database is archived consistently.
opengym_was_running=""
if [ -n "$(docker compose ps -q --status running opengym-api 2>/dev/null)" ]; then
	opengym_was_running=1
	echo "Stopping opengym-api for a consistent backup"
	docker compose stop opengym-api
fi

restart_opengym() {
	if [ -n "$opengym_was_running" ]; then
		echo "Restarting opengym-api"
		docker compose start opengym-api
		opengym_was_running=""
	fi
}
trap restart_opengym EXIT

# openGym media (progress photos under uploads/) is excluded to keep backups
# small; everything else (accounts, passkeys, workouts, secrets) is included.
archive_mount opengym_data centcom_opengym_data --exclude './uploads' --exclude './uploads/*'
restart_opengym

# Remaining named volumes (live archives; their sqlite data is low-write).
archive_mount pihole_data centcom_pihole_data
archive_mount pihole_dnsmasq centcom_pihole_dnsmasq
archive_mount beszel_data centcom_beszel_data
archive_mount beszel_agent_data centcom_beszel_agent_data
archive_mount caddy_data centcom_caddy_data

# Host-path data.
[ -d "$ROOT_DIR/data" ] && archive_mount freshrss_data "$ROOT_DIR/data"
[ -d "$ROOT_DIR/extensions" ] && archive_mount freshrss_extensions "$ROOT_DIR/extensions"
[ -d /opt/golink/data ] && archive_mount golink_data /opt/golink/data

# Secrets: the one file that cannot be regenerated.
if [ -f .env ]; then
	cp .env "$BACKUP_DIR/env"
	chmod 600 "$BACKUP_DIR/env"
fi

echo "Backup written to $BACKUP_DIR"

# Prune old local backups beyond the retention count.
prune_count=0
for dir in $(ls -1d "$BACKUP_ROOT"/*/ 2>/dev/null | sort -r | tail -n "+$((BACKUP_RETENTION + 1))"); do
	rm -rf "$dir"
	prune_count=$((prune_count + 1))
done
[ "$prune_count" -gt 0 ] && echo "Pruned $prune_count old backup(s)"

# Sync the new backup to S3 using the stack's existing AWS credentials.
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
	BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-centcom}"
	echo "Syncing to s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/$TIMESTAMP/"
	if ! docker run --rm --env-file .env -v "$BACKUP_DIR":/backup:ro amazon/aws-cli \
		s3 sync /backup "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/$TIMESTAMP/" --no-progress; then
		echo "✗ S3 sync failed"
		failures="$failures s3-sync"
	fi
else
	echo "BACKUP_S3_BUCKET is not set; skipping S3 sync"
fi

if [ -n "$failures" ]; then
	echo "Backup completed with failures:$failures"
	exit 1
fi

echo "Backup completed successfully"
