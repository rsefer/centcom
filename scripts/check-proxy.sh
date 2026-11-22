#!/bin/sh

set -eu

if [ "$#" -ne 2 ]; then
	echo "Usage: $0 <domain> <proxy-tailscale-ip>" >&2
	exit 1
fi

domain="$1"
ip="$2"
case "$domain" in ''|*[!a-zA-Z0-9.-]*) echo "Invalid domain" >&2; exit 1 ;; esac
case "$ip" in ''|*[!0-9.]*) echo "Invalid IPv4 address" >&2; exit 1 ;; esac

failed=0
services="dashboard ai beszel draw go gym ha pihole rss"
if [ "${CHECK_MCP:-0}" = 1 ]; then
	services="$services gym-mcp"
fi
for service in $services; do
	if [ "$service" = dashboard ]; then
		host="$domain"
	else
		host="$service.$domain"
	fi
	path=/
	if [ "$service" = pihole ]; then path=/admin/; fi
	if [ "$service" = gym-mcp ]; then path=/mcp; fi
	if status="$(curl --noproxy '*' --silent --show-error --connect-timeout 5 --max-time 15 \
		--output /dev/null --write-out '%{http_code}' \
		--resolve "$host:443:$ip" "https://$host$path")"; then
		case "$status" in
			2??|3??|401|403)
				echo "$host$path: HTTP $status"
				;;
			*)
				if [ "$service" = gym-mcp ] && [ "$status" = 405 ]; then
					echo "$host$path: HTTP $status"
				else
					echo "$host$path: unexpected HTTP $status" >&2
					failed=1
				fi
				;;
		esac
	else
		echo "$host$path: connection or TLS failure" >&2
		failed=1
	fi
done
exit "$failed"
