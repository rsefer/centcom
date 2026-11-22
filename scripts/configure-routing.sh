#!/bin/sh

set -eu

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
	echo "Usage: $0 <subnet-cidr>" >&2
	exit 1
fi

printf '%s\n' 'net.ipv4.ip_forward = 1' 'net.ipv6.conf.all.forwarding = 1' |
	sudo tee /etc/sysctl.d/99-centcom-forwarding.conf > /dev/null
sudo sysctl -p /etc/sysctl.d/99-centcom-forwarding.conf
sudo tailscale set --advertise-routes="$1" --advertise-exit-node
if sudo tailscale status --json | grep -q '"tag:centcom"'; then
	echo "Tagged tag:centcom; the subnet route and exit node are auto-approved by tailscale/policy.hujson."
else
	echo "This node is not tagged tag:centcom. Tag it in the admin console (Machines > ... > Edit ACL tags)," >&2
	echo "or authenticate with: sudo tailscale up --advertise-tags=tag:centcom --advertise-exit-node" >&2
fi
