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
script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
probes="$(python3 "$script_dir/services.py" checks failover)"
tab="$(printf '\t')"
while IFS="$tab" read -r service path optional; do
	if [ "$optional" = optional ] && [ "${CHECK_MCP:-0}" != 1 ]; then continue; fi
	if [ "$service" = dashboard ]; then
		host="$domain"
	else
		host="$service.$domain"
	fi
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
done <<EOF
$probes
EOF
exit "$failed"
