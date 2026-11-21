#!/bin/sh

# Backs up all CENTCOM persistent data to a timestamped directory, prunes old
# backups, and optionally syncs the new backup to S3.
#
# Configuration (via .env or environment):
#   BACKUP_ROOT       Local backup destination (default: ~/centcom-backups)
#   BACKUP_RETENTION  Days to retain local backups (default: 100)
#   BACKUP_S3_BUCKET  S3 bucket name; S3 sync is skipped when unset
#   BACKUP_S3_PREFIX  Key prefix inside the bucket (default: centcom)

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

# .env may contain values with spaces (e.g. SSH keys), which are not
# shell-safe, so extract only the backup settings instead of sourcing it.
env_get() {
	grep "^$1=" .env 2>/dev/null | head -n 1 | cut -d= -f2-
}

if [ -f .env ]; then
	BACKUP_ROOT="${BACKUP_ROOT:-$(env_get BACKUP_ROOT)}"
	BACKUP_RETENTION="${BACKUP_RETENTION:-$(env_get BACKUP_RETENTION)}"
	BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-$(env_get BACKUP_S3_BUCKET)}"
	BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-$(env_get BACKUP_S3_PREFIX)}"
fi

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/centcom-backups}"
BACKUP_RETENTION="${BACKUP_RETENTION:-100}"
case "$BACKUP_RETENTION" in
	0*|*[!0-9]*)
		echo "BACKUP_RETENTION must be a positive whole number of days" >&2
		exit 1
		;;
esac
services="$(docker compose config --services)"
has_service() {
	printf '%s\n' "$services" | grep -Fxq "$1"
}
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

# Some services are stopped briefly so their sqlite databases are archived
# consistently; all stopped services are restarted even if the script fails.
stopped_services=""

stop_service() {
	if [ -n "$(docker compose ps -q --status running "$1" 2>/dev/null)" ]; then
		echo "Stopping $1 for a consistent backup"
		docker compose stop "$1"
		stopped_services="$stopped_services $1"
	fi
}

restart_services() {
	for svc in $stopped_services; do
		echo "Restarting $svc"
		docker compose start "$svc"
	done
	stopped_services=""
}
trap restart_services EXIT

if has_service opengym-api; then
stop_service opengym-api

# openGym media (progress photos under uploads/) is excluded to keep backups
# small; everything else (accounts, passkeys, workouts, secrets) is included.
archive_mount opengym_data centcom_opengym_data --exclude './uploads' --exclude './uploads/*'
restart_services
fi

# Remaining named volumes (live archives; their sqlite data is low-write).
# Pi-hole: query history (pihole-FTL.db) and the compiled blocklist
# (gravity.db, rebuilt by `pihole -g`) are excluded. Config is covered by
# pihole.toml plus Pi-hole's own rolling config_backups/ and gravity_backups/
# (config-only gravity snapshots), which are included.
if has_service pihole; then
archive_mount pihole_data centcom_pihole_data \
	--exclude './gravity.db' \
	--exclude './gravity_old.db' \
	--exclude './pihole-FTL.db' \
	--exclude './pihole-FTL.db-wal' \
	--exclude './pihole-FTL.db-shm' \
	--exclude './listsCache' \
	--exclude './listsCache/*'
archive_mount pihole_dnsmasq centcom_pihole_dnsmasq
fi

# Beszel: historical metrics (system_stats, container_stats) are excluded by
# pruning a working copy of the PocketBase database. Monitor definitions
# (systems), users, alert config, and the hub's SSH key are kept. The hub is
# stopped briefly for a consistent copy.
if has_service beszel; then
stop_service beszel
echo "Backing up beszel_data"
if ! docker run --rm -v centcom_beszel_data:/source:ro -v "$BACKUP_DIR":/backup alpine sh -c '
	apk add --no-cache sqlite >/dev/null &&
	cp -a /source /work &&
	sqlite3 /work/data.db "DELETE FROM system_stats; DELETE FROM container_stats; VACUUM;" &&
	rm -f /work/data.db-wal /work/data.db-shm &&
	tar czf /backup/beszel_data.tar.gz -C /work .
'; then
	echo "✗ Failed to back up beszel_data"
	failures="$failures beszel_data"
fi
restart_services

fi
if has_service beszel-agent; then
	archive_mount beszel_agent_data centcom_beszel_agent_data
fi
if has_service caddy; then
archive_mount caddy_data centcom_caddy_data
fi

# Host-path data.
# FreshRSS: saved feed items are excluded (users/*/db.sqlite, cache, favicons).
# Feeds, categories, and per-feed settings are captured by exporting OPML into
# the data dir first; config.php files are included as-is.
if has_service rss && [ -n "$(docker compose ps -q --status running rss 2>/dev/null)" ]; then
	echo "Exporting FreshRSS OPML"
	if ! docker compose exec -T rss sh -c '
		cd /var/www/FreshRSS
		for u in $(./cli/list-users.php); do
			./cli/export-opml-for-user.php --user "$u" > "data/opml-$u.xml"
		done
	'; then
		echo "✗ Failed to export FreshRSS OPML"
		failures="$failures freshrss-opml"
	fi
fi
if has_service rss && [ -d "$ROOT_DIR/data" ]; then
archive_mount freshrss_data "$ROOT_DIR/data" \
	--exclude './cache' \
	--exclude './favicons' \
	--exclude './users/*/db.sqlite'
fi
if has_service rss && [ -d "$ROOT_DIR/extensions" ]; then
	archive_mount freshrss_extensions "$ROOT_DIR/extensions"
fi
if has_service golink && [ -d /opt/golink/data ]; then
	archive_mount golink_data /opt/golink/data
fi

# Secrets: the one file that cannot be regenerated.
if [ -f .env ]; then
	cp .env "$BACKUP_DIR/env"
	chmod 600 "$BACKUP_DIR/env"
fi

echo "Backup written to $BACKUP_DIR"

# Prune timestamped backup directories after their retention period.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
	-name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]' \
	-mtime "+$((BACKUP_RETENTION - 1))" -exec rm -rf -- {} +

# Sync the new backup to S3 using the stack's existing AWS credentials.
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
	BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-centcom}"
	if [ -L "$ROOT_DIR/compose.yaml" ]; then
		host="$(hostname -s)"
		case "$host" in
			''|*[!a-zA-Z0-9._-]*)
				echo "Invalid hostname for host-specific backup prefix" >&2
				exit 1
				;;
		esac
		BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX%/}/$host"
	fi
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
