#!/bin/sh

set -eu

TARGET_HOST="${1:-centcom}"
TARGET_BRANCH="${2:-main}"

ssh "$TARGET_HOST" "
	set -eu
	cd ~/centcom
	current_branch=\$(git rev-parse --abbrev-ref HEAD)
	if [ \"\$current_branch\" != \"$TARGET_BRANCH\" ]; then
		echo 'Refusing deploy: expected branch $TARGET_BRANCH, found' \"\$current_branch\"
		exit 1
	fi
	git fetch origin $TARGET_BRANCH
	git pull --ff-only origin $TARGET_BRANCH
	./reload.sh
"
