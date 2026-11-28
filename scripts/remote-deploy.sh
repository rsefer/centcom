#!/bin/sh

set -eu

TARGET_HOST="${1:-all}"
TARGET_BRANCH="${2:-main}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

case "$TARGET_BRANCH" in
	''|*[!a-zA-Z0-9._/-]*|-*)
		echo "Invalid branch: $TARGET_BRANCH" >&2
		exit 1
		;;
esac
git check-ref-format --branch "$TARGET_BRANCH" >/dev/null
git fetch --quiet origin "$TARGET_BRANCH"
TARGET_COMMIT="$(git rev-parse --verify 'FETCH_HEAD^{commit}')"

if [ "$(git branch --show-current)" = "$TARGET_BRANCH" ] &&
	! git merge-base --is-ancestor HEAD "$TARGET_COMMIT"; then
	echo "Local $TARGET_BRANCH has commits not on GitHub; push them before deploying." >&2
	exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
	echo "Local uncommitted changes will not be deployed."
fi

if [ "$TARGET_HOST" = all ]; then
	HOSTS="$(git ls-tree --name-only "$TARGET_COMMIT" |
		sed -n 's/^docker-compose\.\([a-z0-9][a-z0-9-]*\)\.yml$/\1/p')"
	if [ -z "$HOSTS" ]; then
		echo "No host Compose files found in the published commit." >&2
		exit 1
	fi
else
	case "$TARGET_HOST" in
		''|*[!a-zA-Z0-9.-]*|-*)
			echo "Invalid host: $TARGET_HOST" >&2
			exit 1
			;;
	esac
	HOSTS="$TARGET_HOST"
fi

connect() {
	host="$1"
	shift
	if [ "$TARGET_HOST" = all ]; then
		ssh -o BatchMode=yes -o ConnectTimeout=10 \
			-o "HostName=$host" -o "HostKeyAlias=$host.local" "$host.local" "$@"
	else
		ssh -o BatchMode=yes -o ConnectTimeout=10 "$host" "$@"
	fi
}

for host in $HOSTS; do
	echo "Checking $host"
	expected_host="-"
	if [ "$TARGET_HOST" = all ]; then
		expected_host="$host"
	fi
	connect "$host" sh -s -- "$TARGET_BRANCH" "$TARGET_COMMIT" "$expected_host" "${SKIP_ENV_CHECK:-}" <<'REMOTE'
	set -eu
	cd ~/centcom
	if [ "$3" != "-" ] &&
		{ [ ! -L compose.yaml ] || [ "$(readlink compose.yaml)" != "docker-compose.$3.yml" ]; }; then
		echo "Refusing deploy: compose.yaml does not select $3" >&2
		exit 1
	fi
	if [ "$(git branch --show-current)" != "$1" ]; then
		echo "Refusing deploy: expected branch $1" >&2
		exit 1
	fi
	if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
		echo "Refusing deploy: remote working tree has uncommitted changes" >&2
		exit 1
	fi
	git fetch --quiet origin "$1"
	if [ "$(git rev-parse 'FETCH_HEAD^{commit}')" != "$2" ]; then
		echo "Refusing deploy: GitHub branch changed during preflight" >&2
		exit 1
	fi
	if ! git merge-base --is-ancestor HEAD "$2"; then
		echo "Refusing deploy: remote branch cannot fast-forward" >&2
		exit 1
	fi
	if ! systemctl is-active --quiet cron; then
		echo "Refusing deploy: cron service is not active" >&2
		exit 1
	fi
	# A key the new commit adds to .env.sample (including a renamed one) must
	# exist in this host's .env; a blank value is enough for optional settings.
	# Checking now stops the deploy before any host changes.
	if [ -z "$4" ]; then
		keys='s/^\([A-Z][A-Z0-9_]*\)=.*/\1/p'
		old_keys="$(git show HEAD:.env.sample 2>/dev/null | sed -n "$keys")"
		missing=""
		for key in $(git show "$2:.env.sample" 2>/dev/null | sed -n "$keys"); do
			printf '%s\n' "$old_keys" | grep -qx "$key" && continue
			grep -q "^$key=" .env 2>/dev/null || missing="$missing $key"
		done
		if [ -n "$missing" ]; then
			echo "Refusing deploy: ~/centcom/.env is missing keys this commit adds to .env.sample:$missing" >&2
			echo "Add them (blank is fine for optional settings), or set SKIP_ENV_CHECK=1." >&2
			exit 1
		fi
	fi
REMOTE
done

# Optional snapshot of every host before any of them changes (make deploy BACKUP=1).
# A failed backup stops the deploy while all hosts are still untouched.
if [ -n "${DEPLOY_BACKUP:-}" ]; then
	for host in $HOSTS; do
		echo "Backing up $host before deploying"
		connect "$host" sh -s <<'REMOTE'
	set -eu
	cd ~/centcom
	./scripts/backup.sh
REMOTE
	done
fi

for host in $HOSTS; do
	echo "Deploying $host"
	connect "$host" sh -s -- "$TARGET_COMMIT" <<'REMOTE'
	set -eu
	cd ~/centcom
	if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
		echo "Refusing deploy: remote working tree changed after preflight" >&2
		exit 1
	fi
	# Remember where this host was so `make rollback` can return to it. A ref
	# keeps the working tree clean and is only moved when HEAD changes.
	before="$(git rev-parse HEAD)"
	git merge --ff-only "$1"
	if [ "$(git rev-parse HEAD)" != "$before" ]; then
		git update-ref refs/centcom/previous-deploy "$before"
	fi
	./scripts/setup-cron.sh
	./scripts/reload.sh
REMOTE
done

echo "Checking DNS and service URLs from $host"
connect "$host" sh -s <<'REMOTE'
	set -eu
	cd ~/centcom
	python3 scripts/check-services.py
REMOTE
