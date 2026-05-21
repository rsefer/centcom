#!/bin/sh

set -eu

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

docker compose pull --ignore-buildable
docker compose build caddy
docker compose up -d --remove-orphans

wait_for_service caddy 90
wait_for_service rss 90
