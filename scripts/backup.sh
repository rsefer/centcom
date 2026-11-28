#!/bin/sh

# Backs up all CENTCOM persistent data to a timestamped directory, prunes old
# backups, and optionally syncs the new backup to S3.
#
# Configuration (via .env or environment):
#   BACKUP_ROOT       Local backup destination (default: ~/centcom-backups)
#   BACKUP_RETENTION  Days to retain local backups (default: 100)
#   BACKUP_S3_BUCKET  S3 bucket name; S3 sync is skipped when unset
#   BACKUP_S3_PREFIX  Key prefix inside the bucket (default: centcom)
#   BACKUP_PING_URL   Dead-man's-switch URL (for example a healthchecks.io check).
#                     Requested after a successful backup, with /fail added to the
#                     path after a failed one. "{host}" becomes the short hostname,
#                     so one shared .env can serve every host.
#
# A failed backup also emails DNS_ALERT_TO (see scripts/alerts.py), and a successful
# one records the time in $BACKUP_ROOT/.last-success for scripts/alert-watch.py.

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
	BACKUP_PING_URL="${BACKUP_PING_URL:-$(env_get BACKUP_PING_URL)}"
fi

# Alerts must never change the backup's own result.
ping_backup() {
	[ -n "${BACKUP_PING_URL:-}" ] || return 0
	if ! command -v curl >/dev/null 2>&1; then
		echo "curl not found; skipping the backup ping" >&2
		return 0
	fi
	ping_base="$(printf '%s' "${BACKUP_PING_URL%%\?*}" | sed "s|{host}|$(hostname -s)|g")"
	ping_query="${BACKUP_PING_URL#"${BACKUP_PING_URL%%\?*}"}"
	curl -fsS -m 10 --retry 3 -o /dev/null "$ping_base$1$ping_query" ||
		echo "Backup ping to $ping_base$1 failed" >&2
	return 0
}

notify_failure() {
	python3 "$SCRIPT_DIR/alerts.py" "[centcom] backup FAILED on $(hostname -s)" <<ALERT || true
The backup on $(hostname -s) exited with status $1 at $(date '+%F %T').
Failed parts:${failures:- (none recorded; see the log)}

Last lines of ~/centcom/backup.log:
$(tail -n 30 "$ROOT_DIR/backup.log" 2>/dev/null)
ALERT
}

# Stopped services are restarted even if the script fails; a failure is reported.
on_exit() {
	status=$?
	if type restart_services >/dev/null 2>&1; then
		restart_services || true
	fi
	if [ "$status" -ne 0 ]; then
		ping_backup /fail
		notify_failure "$status"
	fi
	exit "$status"
}
trap on_exit EXIT

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
	case "$source" in
		/*)
			if [ ! -d "$source" ]; then
				echo "✗ Missing directory for $name: $source" >&2
				failures="$failures $name"
				return 0
			fi
			;;
		*)
			if ! docker volume inspect "$source" >/dev/null 2>&1; then
				echo "✗ Missing or inaccessible volume for $name: $source" >&2
				failures="$failures $name"
				return 0
			fi
			;;
	esac
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
	running="$(docker compose ps -q --status running "$1")"
	if [ -n "$running" ]; then
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

if has_service opengym-api; then
stop_service opengym-api

# openGym media (progress photos under uploads/) is excluded to keep backups
# small; everything else (accounts, passkeys, workouts, secrets) is included.
archive_mount opengym_data centcom_opengym_data --exclude './uploads' --exclude './uploads/*'
restart_services
fi

# Karakeep: db.db (bookmarks, lists, tags, highlights, users, API keys) and
# assets/ (saved page text over 5 KB, screenshots, banners, uploads) are kept.
# queue.db (background job queue) is excluded; Meilisearch is not backed up
# at all, since its index is rebuilt from db.db (Admin > Reindex). The app is
# stopped briefly so the WAL is checkpointed into db.db.
if has_service karakeep; then
stop_service karakeep
archive_mount karakeep_data centcom_karakeep_data \
	--exclude './queue.db' \
	--exclude './queue.db-wal' \
	--exclude './queue.db-shm'
restart_services
fi

# Vaultwarden: stop briefly so its SQLite database, encryption keys, and
# attachments are captured as one consistent backup.
if has_service vaultwarden; then
	stop_service vaultwarden
	archive_mount vaultwarden_data centcom_vaultwarden_data
	restart_services
fi

# Remaining named volumes (live archives; their sqlite data is low-write).
# Pi-hole: query history (pihole-FTL.db) and the compiled blocklist are
# excluded. gravity.db also holds adlists, allow/deny rules, groups and clients
# (not rebuildable), so a consistent config-only copy (gravity-config.db: live
# DB via sqlite .backup with the gravity/antigravity tables emptied) is made
# first and archived alongside pihole.toml and Pi-hole's config_backups/.
# gravity_backups/ is excluded: it rotates hourly and fills with whatever state
# gravity last ran against.
if has_service pihole; then
pihole_cfg=/etc/pihole/gravity-config.db
if docker compose exec -T pihole sh -c "rm -f $pihole_cfg && pihole-FTL sqlite3 /etc/pihole/gravity.db \".backup $pihole_cfg\" && pihole-FTL sqlite3 $pihole_cfg 'delete from gravity; delete from antigravity; vacuum;'"; then
	adlists="$(docker compose exec -T pihole pihole-FTL sqlite3 -readonly $pihole_cfg 'select count(*) from adlist;' | tr -d '\r')"
	count_file="$BACKUP_ROOT/.pihole-adlist-count"
	previous="$(cat "$count_file" 2>/dev/null || echo 0)"
	echo "Pi-hole adlists: $adlists (previous backup: $previous)"
	if [ "${adlists:-0}" -lt "$previous" ]; then
		echo "✗ Pi-hole adlist count dropped from $previous to $adlists" >&2
		failures="$failures pihole_adlists"
	fi
	echo "$adlists" >"$count_file"
else
	echo "✗ Failed to create Pi-hole gravity-config.db" >&2
	failures="$failures pihole_gravity_config"
fi
archive_mount pihole_data centcom_pihole_data \
	--exclude './gravity.db' \
	--exclude './gravity_old.db' \
	--exclude './gravity_backups' \
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
if ! docker volume inspect centcom_beszel_data >/dev/null 2>&1; then
	echo "✗ Missing or inaccessible volume for beszel_data" >&2
	failures="$failures beszel_data"
elif ! docker run --rm -v centcom_beszel_data:/source:ro -v "$BACKUP_DIR":/backup alpine sh -c '
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
if has_service rss; then
	if [ -z "$(docker compose ps -q --status running rss)" ]; then
		echo "✗ FreshRSS is not running; cannot export OPML" >&2
		failures="$failures freshrss-opml"
	else
		echo "Exporting FreshRSS OPML"
		if ! docker compose exec -T rss sh -c '
			set -e
			cd /var/www/FreshRSS
			users="$(./cli/list-users.php)"
			for u in $users; do
				tmp="data/.opml-$u.xml.tmp"
				if ./cli/export-opml-for-user.php --user "$u" > "$tmp" && [ -s "$tmp" ]; then
					mv "$tmp" "data/opml-$u.xml"
				else
					rm -f "$tmp"
					exit 1
				fi
			done
		'; then
			echo "✗ Failed to export FreshRSS OPML" >&2
			failures="$failures freshrss-opml"
		fi
	fi
	archive_mount freshrss_data "$ROOT_DIR/data" \
		--exclude './cache' \
		--exclude './favicons' \
		--exclude './users/*/db.sqlite'
	archive_mount freshrss_extensions "$ROOT_DIR/extensions"
fi
if has_service golink; then
	archive_mount golink_data /opt/golink/data
fi

# Secrets: the one file that cannot be regenerated.
if [ -f .env ]; then
	cp .env "$BACKUP_DIR/env"
	chmod 600 "$BACKUP_DIR/env"
fi

echo "Backup written to $BACKUP_DIR"

if [ -n "$failures" ]; then
	echo "Backup incomplete:$failures; not pruning older backups or syncing to S3" >&2
	exit 1
fi

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
	if ! docker run --rm --env-file .env -v "$BACKUP_DIR":/backup:ro amazon/aws-cli:2.27.49 \
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

# Prune timestamped backup directories after their retention period.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
	-name '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]' \
	-mtime "+$((BACKUP_RETENTION - 1))" -exec rm -rf -- {} +

offsite=false
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
	offsite=true
fi
docker run --rm -v centcom_status_data:/status alpine sh -c '
	printf "{\"last_success\":\"%s\",\"offsite\":%s}\n" "$1" "$2" > /status/last-backup.tmp &&
	mv /status/last-backup.tmp /status/last-backup.json
' sh "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$offsite"

date -u +%Y-%m-%dT%H:%M:%SZ > "$BACKUP_ROOT/.last-success"
ping_backup ""

echo "Backup completed successfully"
