#!/bin/sh

# Encrypted, standalone backup of the Vaultwarden vault. Runs daily from cron on the
# host that runs Vaultwarden (southcom), separately from scripts/backup.sh.
#
# Why separate: backup.sh skips its S3 upload when any part of the run fails, and its
# archives are not encrypted. This job snapshots the live database (SQLite .backup,
# no downtime, checked with PRAGMA integrity_check), adds the RSA keys, attachments,
# sends and config, and encrypts everything with age to your PUBLIC key. Only the
# holder of the matching private key can open it, so S3 holds nothing readable.
#
# Configuration (via .env or environment):
#   VAULTWARDEN_AGE_RECIPIENT  age public key(s), age1..., separated by commas or
#                              spaces. Required: the job does nothing without it.
#   VAULTWARDEN_BACKUP_KEEP    Encrypted files to keep locally (default: 30)
#   BACKUP_ROOT                Local destination (default: ~/centcom-backups); files go
#                              in $BACKUP_ROOT/vaultwarden/
#   BACKUP_S3_BUCKET, BACKUP_S3_PREFIX  As for backup.sh; files go to
#                              <prefix>/<host>/vaultwarden/ and expire with the bucket rule
#   VAULTWARDEN_VOLUME         Docker volume to read (default: centcom_vaultwarden_data)
#
# A failure emails DNS_ALERT_TO (scripts/alerts.py); success records the time in
# $BACKUP_ROOT/vaultwarden/.last-success for scripts/alert-watch.py.
# Restore: see docs/backups.md.

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

env_get() {
	grep "^$1=" .env 2>/dev/null | head -n 1 | cut -d= -f2-
}

if [ -f .env ]; then
	BACKUP_ROOT="${BACKUP_ROOT:-$(env_get BACKUP_ROOT)}"
	BACKUP_S3_BUCKET="${BACKUP_S3_BUCKET:-$(env_get BACKUP_S3_BUCKET)}"
	BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-$(env_get BACKUP_S3_PREFIX)}"
	VAULTWARDEN_AGE_RECIPIENT="${VAULTWARDEN_AGE_RECIPIENT:-$(env_get VAULTWARDEN_AGE_RECIPIENT)}"
	VAULTWARDEN_BACKUP_KEEP="${VAULTWARDEN_BACKUP_KEEP:-$(env_get VAULTWARDEN_BACKUP_KEEP)}"
fi
BACKUP_ROOT="${BACKUP_ROOT:-$HOME/centcom-backups}"
VAULTWARDEN_BACKUP_KEEP="${VAULTWARDEN_BACKUP_KEEP:-30}"
VAULTWARDEN_VOLUME="${VAULTWARDEN_VOLUME:-centcom_vaultwarden_data}"

if [ -z "${VAULTWARDEN_AGE_RECIPIENT:-}" ]; then
	echo "VAULTWARDEN_AGE_RECIPIENT is not set; skipping the Vaultwarden backup"
	exit 0
fi
if ! docker compose config --services | grep -Fxq vaultwarden; then
	echo "Vaultwarden is not configured on this host; skipping"
	exit 0
fi

partial=""
notify_failure() {
	python3 "$SCRIPT_DIR/alerts.py" "[centcom] Vaultwarden backup FAILED on $(hostname -s)" <<ALERT || true
The Vaultwarden backup on $(hostname -s) exited with status $1 at $(date '+%F %T').

Last lines of ~/centcom/backup-vaultwarden.log:
$(tail -n 30 "$ROOT_DIR/backup-vaultwarden.log" 2>/dev/null)
ALERT
}
on_exit() {
	status=$?
	if [ -n "$partial" ]; then
		rm -f "$partial"
	fi
	if [ "$status" -ne 0 ]; then
		notify_failure "$status"
	fi
	exit "$status"
}
trap on_exit EXIT

keep="$VAULTWARDEN_BACKUP_KEEP"
case "$keep" in
	''|0*|*[!0-9]*)
		echo "VAULTWARDEN_BACKUP_KEEP must be a positive whole number" >&2
		exit 1
		;;
esac
recipients="$(printf '%s' "$VAULTWARDEN_AGE_RECIPIENT" | tr ',' ' ' | tr -s ' ')"
for recipient in $recipients; do
	case "$recipient" in
		age1*[!a-z0-9]*|age1)
			echo "VAULTWARDEN_AGE_RECIPIENT must be age public keys (age1...)" >&2
			exit 1
			;;
		age1*) ;;
		*)
			echo "VAULTWARDEN_AGE_RECIPIENT must be age public keys (age1...), not a private key or passphrase" >&2
			exit 1
			;;
	esac
done

host="$(hostname -s)"
timestamp="$(date +%Y%m%d-%H%M%S)"
dest="$BACKUP_ROOT/vaultwarden"
name="vaultwarden-$host-$timestamp.tar.gz.age"
mkdir -p "$dest"
chmod 700 "$BACKUP_ROOT" "$dest"
partial="$dest/.$name.partial"

echo "Backing up Vaultwarden ($VAULTWARDEN_VOLUME) to $dest/$name"
# The volume is mounted read-write only because SQLite needs its WAL index files;
# nothing here changes the vault. The archive is written to stdout, so no plaintext
# copy of the vault is ever stored.
umask 077
docker run --rm -v "$VAULTWARDEN_VOLUME":/data -e "RECIPIENTS=$recipients" alpine sh -c '
	set -eu
	apk add --no-cache age sqlite >/dev/null
	mkdir /work
	sqlite3 /data/db.sqlite3 ".backup /work/db.sqlite3"
	if [ "$(sqlite3 /work/db.sqlite3 "PRAGMA integrity_check;")" != ok ]; then
		echo "Vaultwarden database failed its integrity check" >&2
		exit 1
	fi
	tar -C /data --exclude=./db.sqlite3 --exclude=./db.sqlite3-wal --exclude=./db.sqlite3-shm \
		--exclude=./icon_cache --exclude=./tmp -cf - . | tar -C /work -xf -
	args=""
	for recipient in $RECIPIENTS; do args="$args -r $recipient"; done
	tar -C /work -czf /tmp/vault.tar.gz .
	age $args -o /tmp/vault.age /tmp/vault.tar.gz
	cat /tmp/vault.age
' > "$partial"

# An encrypted age file starts with this header; an empty or error output does not.
if [ "$(head -c 21 "$partial")" != "age-encryption.org/v1" ] || [ "$(wc -c < "$partial")" -lt 200 ]; then
	echo "The Vaultwarden backup is not a valid age file" >&2
	exit 1
fi
mv "$partial" "$dest/$name"
partial=""
chmod 600 "$dest/$name"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
	BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX:-centcom}"
	if [ -L "$ROOT_DIR/compose.yaml" ]; then
		BACKUP_S3_PREFIX="${BACKUP_S3_PREFIX%/}/$host"
	fi
	echo "Uploading to s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/vaultwarden/$name"
	docker run --rm --env-file .env -v "$dest":/backup:ro amazon/aws-cli:2.27.49 \
		s3 cp "/backup/$name" "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/vaultwarden/$name" --no-progress
else
	echo "BACKUP_S3_BUCKET is not set; keeping the encrypted backup on this host only"
fi

# Keep the newest files only.
ls -1t "$dest"/vaultwarden-*.tar.gz.age | tail -n +"$((keep + 1))" | while read -r old; do
	rm -f -- "$old"
done

date -u +%Y-%m-%dT%H:%M:%SZ > "$dest/.last-success"
echo "Vaultwarden backup completed successfully"
