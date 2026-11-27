#!/bin/sh

set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

TAILSCALE_IP="$(tailscale ip -4)"
if [ -z "$TAILSCALE_IP" ]; then
	echo "Could not determine the host's Tailscale IPv4 address"
	exit 1
fi
export TAILSCALE_IP

wait_for_service() {
	service="$1"
	timeout_seconds="${2:-60}"
	start_time="$(date +%s)"

	container_id="$(docker compose ps -q "$service")"
	if [ -z "$container_id" ]; then
		echo "Service '$service' has no running container"
		return 1
	fi

	while true; do
		health_status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$container_id")"
		if [ "$health_status" = "healthy" ] || [ "$health_status" = "no-healthcheck" ]; then
			echo "Service '$service' is ready ($health_status)"
			return 0
		fi

		now="$(date +%s)"
		if [ $((now - start_time)) -ge "$timeout_seconds" ]; then
			echo "Service '$service' did not become ready in ${timeout_seconds}s (status: $health_status)"
			return 1
		fi

		sleep 2
	done
}

# Generate Caddy routes/upstreams and dashboard data before building or starting services.
python3 scripts/services.py generate
# A rollback sets RELOAD_SKIP_PULL so it restores configuration without
# fetching newer images.
if [ -z "${RELOAD_SKIP_PULL:-}" ]; then
	docker compose pull --ignore-buildable
fi
docker compose build
docker compose up -d --remove-orphans

services="$(docker compose config --services)"
if printf '%s\n' "$services" | grep -Fxq status-agent; then
	docker compose up -d --no-deps --force-recreate status-agent
fi
if printf '%s\n' "$services" | grep -Fxq caddy; then
	docker compose up -d --no-deps --force-recreate caddy
fi
# FreshRSS reads ./extensions on every request, so no restart is needed. A
# failed install (e.g. GitHub unreachable) must not abort the rest of the reload.
if printf '%s\n' "$services" | grep -Fxq rss; then
	sh scripts/install-freshrss-extensions.sh || echo "Warning: FreshRSS extension install failed"
fi
for service in caddy rss pihole; do
	if printf '%s\n' "$services" | grep -Fxq "$service"; then
		wait_for_service "$service" 90
	fi
done
