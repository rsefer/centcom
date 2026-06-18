#!/bin/sh

set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT_DIR"

wait_for_docker() {
	timeout_seconds="${1:-120}"
	start_time="$(date +%s)"

	while true; do
		if docker info >/dev/null 2>&1; then
			echo "Docker daemon is ready"
			return 0
		fi

		now="$(date +%s)"
		if [ $((now - start_time)) -ge "$timeout_seconds" ]; then
			echo "Docker daemon did not become ready in ${timeout_seconds}s"
			return 1
		fi

		sleep 3
	done
}

wait_for_docker 180

if ! docker network inspect caddy_net >/dev/null 2>&1; then
	echo "Creating missing network: caddy_net"
	docker network create caddy_net >/dev/null
fi

docker compose up -d --remove-orphans
