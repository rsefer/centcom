#!/bin/sh
# Install the FreshRSS extensions pinned in services/freshrss/extensions.conf
# into ./extensions. Idempotent: an extension already at its pinned commit is
# left alone. A commit of "latest" tracks the repository's default branch.
# Extensions this script installed earlier (marked by .centcom-ref) are removed
# once they leave the manifest; extensions it did not install are never touched.

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
MANIFEST="${EXTENSIONS_MANIFEST:-$ROOT_DIR/services/freshrss/extensions.conf}"
DEST="${EXTENSIONS_DIR:-$ROOT_DIR/extensions}"

[ -f "$MANIFEST" ] || exit 0
mkdir -p "$DEST" 2>/dev/null || true

# Docker creates ./extensions as root when the directory is missing, so the
# deploying user cannot write to it. Hand it to that user (group www-data, which
# FreshRSS runs as) through the running container; docker group membership is
# enough, no sudo needed.
if [ ! -w "$DEST" ] && [ "$DEST" = "$ROOT_DIR/extensions" ]; then
	echo "Fixing ownership of $DEST"
	(cd "$ROOT_DIR" && docker compose exec -T -u root rss \
		chown "$(id -u):33" /var/www/FreshRSS/extensions) || true
fi
if [ ! -w "$DEST" ]; then
	echo "✗ $DEST is not writable by $(id -un)" >&2
	exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

failed=0
wanted=" "
while read -r repo sha subdir extra; do
	case "$repo" in ''|'#'*) continue ;; esac
	if [ -z "$sha" ] || [ -z "$subdir" ] || [ -n "$extra" ]; then
		echo "✗ Malformed manifest line for $repo (want: <repo> <sha> <subdir>)" >&2
		failed=1; continue
	fi
	if [ "$sha" = latest ]; then
		if ! sha="$(git ls-remote "$repo" HEAD | cut -f1)" || [ -z "$sha" ]; then
			echo "✗ Could not resolve the latest commit of $repo" >&2
			failed=1; continue
		fi
	elif ! printf '%s' "$sha" | grep -Eq '^[0-9a-f]{40}$'; then
		echo "✗ $repo: '$sha' is not 'latest' or a full commit SHA" >&2
		failed=1; continue
	fi
	case "$subdir" in /*|*..*) echo "✗ $repo: bad subdirectory '$subdir'" >&2; failed=1; continue ;; esac

	if [ "$subdir" = . ]; then
		name="$(basename "$repo" .git)"
	else
		name="$(basename "$subdir")"
	fi
	target="$DEST/$name"
	wanted="$wanted$name "
	marker="$sha"
	if [ -f "$target/.centcom-ref" ] && [ "$(cat "$target/.centcom-ref")" = "$marker" ]; then
		echo "FreshRSS extension $name already at ${sha%"${sha#???????}"}"
		continue
	fi

	echo "Installing FreshRSS extension $name from $repo @ $sha"
	src="$work/$name"
	if ! { git init -q "$src" &&
		git -C "$src" fetch -q --depth 1 "$repo" "$sha" &&
		git -C "$src" checkout -q FETCH_HEAD; }; then
		echo "✗ Could not fetch $repo @ $sha" >&2
		failed=1; continue
	fi
	if [ ! -f "$src/$subdir/metadata.json" ]; then
		echo "✗ $repo has no $subdir/metadata.json; not a FreshRSS extension" >&2
		failed=1; continue
	fi

	stage="$DEST/.$name.new"
	rm -rf "$stage"
	mkdir "$stage"
	# Copy the extension without git metadata.
	(cd "$src/$subdir" && tar --exclude=.git -cf - .) | (cd "$stage" && tar -xf -)
	printf '%s\n' "$marker" > "$stage/.centcom-ref"
	chmod -R a+rX "$stage"
	rm -rf "$target"
	mv "$stage" "$target"
done < "$MANIFEST"

# Prune extensions we installed that are no longer in the manifest. Skipped when
# any line failed, so a typo never deletes a working extension.
if [ "$failed" = 0 ]; then
	for dir in "$DEST"/*/; do
		[ -f "${dir}.centcom-ref" ] || continue
		name="$(basename "$dir")"
		case "$wanted" in *" $name "*) continue ;; esac
		echo "Removing FreshRSS extension $name (no longer in the manifest)"
		rm -rf "$DEST/$name"
	done
fi

exit "$failed"
