#!/bin/sh

# Restores a single archive produced by scripts/backup.sh into its Docker
# volume or host path. The target's existing contents are replaced.
#
# Usage: ./scripts/restore.sh <path/to/archive.tar.gz>
# A gzip-compressed archive downloaded with a .tar suffix is also supported.
# Example: ./scripts/restore.sh ~/centcom-backups/20260101-031500/opengym_data.tar.gz
#
# Note: opengym_data backups exclude uploaded media (uploads/), so restored
# instances will be missing progress photos.
#
# Stop the service that owns the data first, e.g.:
#   docker compose stop opengym-api   (for opengym_data)
#   docker compose stop pihole        (for pihole_data / pihole_dnsmasq)

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
	echo "Usage: $0 <path/to/archive.tar.gz>"
	exit 1
fi

ARCHIVE="$(CDPATH= cd -- "$(dirname -- "$1")" && pwd)/$(basename "$1")"
NAME="$(basename "$ARCHIVE")"
case "$NAME" in
	*.tar.gz) NAME="${NAME%.tar.gz}" ;;
	*.tar) NAME="${NAME%.tar}" ;;
	*) echo "Expected a .tar.gz or .tar backup archive" >&2; exit 1 ;;
esac

case "$NAME" in
	opengym_data|pihole_data|pihole_dnsmasq|beszel_data|beszel_agent_data|caddy_data)
		TARGET="centcom_$NAME"
		TARGET_KIND="volume"
		;;
	freshrss_data)
		TARGET="$ROOT_DIR/data"
		TARGET_KIND="path"
		;;
	freshrss_extensions)
		TARGET="$ROOT_DIR/extensions"
		TARGET_KIND="path"
		;;
	golink_data)
		TARGET="/opt/golink/data"
		TARGET_KIND="path"
		;;
	*)
		echo "Unknown archive '$NAME'; cannot determine restore target"
		exit 1
		;;
esac

echo "This will REPLACE all contents of $TARGET_KIND '$TARGET'"
printf "Type 'yes' to continue: "
read -r answer
if [ "$answer" != "yes" ]; then
	echo "Aborted"
	exit 1
fi

if [ "$TARGET_KIND" = "path" ]; then
	mkdir -p "$TARGET"
fi

docker run --rm \
	-v "$TARGET":/target \
	-v "$(dirname "$ARCHIVE")":/backup:ro \
	alpine sh -c "find /target -mindepth 1 -delete && tar xzf '/backup/$(basename "$ARCHIVE")' -C /target"

echo "Restored $NAME to $TARGET"
echo "Start the owning service again, e.g.: docker compose up -d"
