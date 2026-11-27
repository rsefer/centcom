#!/bin/sh

# Return hosts to an earlier commit and reload them. Counterpart to
# remote-deploy.sh, with the same host discovery and preflight rules.
#
# Usage: remote-rollback.sh [host|all] [branch] [commit]
#
# Without a commit, each host returns to the commit it ran before its last
# deploy (refs/centcom/previous-deploy, recorded by remote-deploy.sh). With
# "all", the hosts must agree on that commit or a commit must be given.
# The target must be an ancestor of the host's current commit.

set -eu

TARGET_HOST="${1:-all}"
TARGET_BRANCH="${2:-main}"
TARGET_COMMIT="${3:-}"

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

case "$TARGET_BRANCH" in
	''|*[!a-zA-Z0-9._/-]*|-*)
		echo "Invalid branch: $TARGET_BRANCH" >&2
		exit 1
		;;
esac
case "$TARGET_COMMIT" in
	*[!0-9a-f]*)
		echo "Invalid commit: $TARGET_COMMIT (use a hexadecimal commit id)" >&2
		exit 1
		;;
esac
if [ -n "$TARGET_COMMIT" ] && [ "${#TARGET_COMMIT}" -lt 7 ]; then
	echo "Invalid commit: $TARGET_COMMIT (use at least 7 characters)" >&2
	exit 1
fi
git check-ref-format --branch "$TARGET_BRANCH" >/dev/null

if [ "$TARGET_HOST" = all ]; then
	git fetch --quiet origin "$TARGET_BRANCH"
	HOSTS="$(git ls-tree --name-only 'FETCH_HEAD^{commit}' |
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

# Preflight every host before changing any. Each host prints the full commit
# it would return to as its only standard output.
targets=""
for host in $HOSTS; do
	echo "Checking $host"
	expected_host="-"
	if [ "$TARGET_HOST" = all ]; then
		expected_host="$host"
	fi
	target="$(connect "$host" sh -s -- "$TARGET_BRANCH" "$TARGET_COMMIT" "$expected_host" <<'REMOTE'
	set -eu
	cd ~/centcom
	if [ "$3" != "-" ] &&
		{ [ ! -L compose.yaml ] || [ "$(readlink compose.yaml)" != "docker-compose.$3.yml" ]; }; then
		echo "Refusing rollback: compose.yaml does not select $3" >&2
		exit 1
	fi
	if [ "$(git branch --show-current)" != "$1" ]; then
		echo "Refusing rollback: expected branch $1" >&2
		exit 1
	fi
	if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
		echo "Refusing rollback: remote working tree has uncommitted changes" >&2
		exit 1
	fi
	if [ -n "$2" ]; then
		wanted="$2"
	elif git rev-parse --verify --quiet refs/centcom/previous-deploy >/dev/null; then
		wanted=refs/centcom/previous-deploy
	else
		echo "Refusing rollback: no previous deploy is recorded; pass a commit (TO=...)" >&2
		exit 1
	fi
	if ! target="$(git rev-parse --verify --quiet "$wanted^{commit}")"; then
		echo "Refusing rollback: commit $wanted is not in this host's repository" >&2
		exit 1
	fi
	if [ "$target" = "$(git rev-parse HEAD)" ]; then
		echo "Refusing rollback: already at $target" >&2
		exit 1
	fi
	if ! git merge-base --is-ancestor "$target" HEAD; then
		echo "Refusing rollback: $target is not an ancestor of the checked-out commit" >&2
		exit 1
	fi
	if ! systemctl is-active --quiet cron; then
		echo "Refusing rollback: cron service is not active" >&2
		exit 1
	fi
	echo "$target"
REMOTE
	)"
	echo "  $host: roll back to $target"
	targets="$targets $target"
done

if [ -z "$TARGET_COMMIT" ] && [ "$(printf '%s\n' $targets | sort -u | wc -l)" -gt 1 ]; then
	echo "Hosts would return to different commits; pass a commit (TO=...) to roll all back to one." >&2
	exit 1
fi

for host in $HOSTS; do
	echo "Rolling back $host"
	connect "$host" sh -s -- "$TARGET_COMMIT" <<'REMOTE'
	set -eu
	cd ~/centcom
	if [ -n "$(git status --porcelain --untracked-files=normal)" ]; then
		echo "Refusing rollback: remote working tree changed after preflight" >&2
		exit 1
	fi
	if [ -n "$1" ]; then
		target="$(git rev-parse --verify "$1^{commit}")"
	else
		target="$(git rev-parse --verify "refs/centcom/previous-deploy^{commit}")"
	fi
	# The tree is clean, so nothing is lost; the commits stay in origin and the reflog.
	git reset --hard "$target"
	./scripts/setup-cron.sh
	# Restore configuration only; do not pull newer images during a rollback.
	RELOAD_SKIP_PULL=1 ./scripts/reload.sh
REMOTE
done

echo "Checking DNS and service URLs from $host"
connect "$host" sh -s <<'REMOTE'
	set -eu
	cd ~/centcom
	python3 scripts/check-services.py
REMOTE
