#!/bin/bash
# Install the Pi-hole -> Tailscale DNS failover checker as a systemd timer (every
# minute). Run on a host that is NOT a Pi-hole (southcom), from the repo checkout,
# as the login user. Needs TS_DNS_OAUTH_*, SMTP_* and DNS_ALERT_* in .env (see .env.sample).
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
USER_NAME="$(id -un)"
[ -f "$ROOT/.env" ] || { echo "Missing $ROOT/.env" >&2; exit 1; }
for key in TS_DNS_OAUTH_CLIENT_ID TS_DNS_OAUTH_CLIENT_SECRET DNS_ALERT_FROM DNS_ALERT_TO SMTP_HOST SMTP_USERNAME SMTP_PASSWORD; do
  grep -q "^$key=." "$ROOT/.env" || echo "warning: $key is not set in .env" >&2
done

if grep -Eiq '^PROXY_FAILOVER=(on|true|1|yes)' "$ROOT/.env"; then
  for key in ROUTE53_HOSTED_ZONE_ID AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY VPN_DOMAIN; do
    grep -q "^$key=." "$ROOT/.env" || echo "warning: $key is not set in .env (needed by PROXY_FAILOVER)" >&2
  done
fi

sudo tee /etc/systemd/system/centcom-dns-failover.service > /dev/null <<UNIT
[Unit]
Description=centcom Pi-hole to Tailscale DNS failover
After=network-online.target tailscaled.service

[Service]
Type=oneshot
User=$USER_NAME
StateDirectory=centcom-dns-failover
ExecStart=/usr/bin/python3 $SCRIPT_DIR/dns-failover.py
TimeoutStartSec=120
UNIT
sudo tee /etc/systemd/system/centcom-dns-failover.timer > /dev/null <<'UNIT'
[Unit]
Description=Run the centcom DNS failover check every minute

[Timer]
OnBootSec=2min
OnUnitInactiveSec=1min
AccuracySec=5s

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now centcom-dns-failover.timer
echo "Installed. Logs: journalctl -u centcom-dns-failover -f"
echo "Pause changes: sudo touch /var/lib/centcom-dns-failover/paused   Resume: sudo rm it"
