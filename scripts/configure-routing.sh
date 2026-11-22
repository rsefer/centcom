#!/bin/sh

set -eu

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
	echo "Usage: $0 <subnet-cidr>" >&2
	exit 1
fi

printf '%s\n' 'net.ipv4.ip_forward = 1' 'net.ipv6.conf.all.forwarding = 1' |
	sudo tee /etc/sysctl.d/99-centcom-forwarding.conf > /dev/null
sudo sysctl -p /etc/sysctl.d/99-centcom-forwarding.conf
sudo tailscale set --advertise-tags=tag:centcom --advertise-routes="$1" --advertise-exit-node
echo "Tagged tag:centcom (key expiry is disabled for tagged devices). With tailscale/policy.hujson"
echo "applied, the subnet route and exit node are auto-approved; otherwise approve them in the admin console."
