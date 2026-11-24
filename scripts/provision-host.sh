#!/bin/sh
# Turns a fresh Raspberry Pi OS install into a centcom host: hostname, Docker,
# Tailscale (tagged, exit node, subnet route), hardening, cron and the stack.
# Run on the Pi from the repo checkout as the login user, with the shared .env
# already in place (scripts/new-host.sh does all of that over SSH).
# Idempotent: finished steps are skipped, so a failed run can be repeated.
#
# Usage: ./scripts/provision-host.sh <host> [--reboot]
#   --reboot   reboot at the end so log2ram takes over /var/log and the
#              @reboot recovery job is exercised.
# Optional .env keys: TS_AUTHKEY (skips the browser login), LAN_SUBNET
# (default 192.168.4.0/22).

set -eu

usage() {
  echo "Usage: $0 <host> [--reboot]" >&2
  exit 1
}

HOST=""
REBOOT=false
STAGE2=false
for arg in "$@"; do
  case "$arg" in
    --reboot) REBOOT=true ;;
    --stage2) STAGE2=true ;;
    -*) usage ;;
    *) [ -z "$HOST" ] || usage; HOST="$arg" ;;
  esac
done
[ -n "$HOST" ] || usage
case "$HOST" in
  *[!a-z0-9-]*|-*) echo "Invalid host: $HOST" >&2; exit 1 ;;
esac

if [ "$(id -u)" -eq 0 ]; then
  echo "Run as the login user, not root." >&2
  exit 1
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

[ -f .env ] || { echo "Missing .env; copy the shared .env into $(pwd) first." >&2; exit 1; }
[ -f "docker-compose.$HOST.yml" ] || { echo "No docker-compose.$HOST.yml in this checkout." >&2; exit 1; }
grep -Eq "^hosts = .*\"$HOST\"" services.toml || { echo "$HOST is not listed in services.toml." >&2; exit 1; }

env_value() {
  sed -n "s/^$1=//p" .env | tail -n 1
}

if [ "$STAGE2" = false ]; then
  echo "==> Hostname"
  if [ "$(hostname)" != "$HOST" ]; then
    sudo hostnamectl set-hostname "$HOST"
    if grep -q '^127\.0\.1\.1' /etc/hosts; then
      sudo sed -i "s/^127\.0\.1\.1.*/127.0.1.1\t$HOST/" /etc/hosts
    else
      printf '127.0.1.1\t%s\n' "$HOST" | sudo tee -a /etc/hosts > /dev/null
    fi
  fi

  echo "==> Compose selection"
  ln -sfn "docker-compose.$HOST.yml" compose.yaml

  echo "==> Packages, Docker and Tailscale"
  sudo apt-get update
  sudo apt-get install -y git python3 curl ca-certificates
  ./scripts/setup.sh --bootstrap-only

  # The new docker group membership only applies to new sessions.
  if ! docker info > /dev/null 2>&1; then
    REBOOT_FLAG=""
    [ "$REBOOT" = true ] && REBOOT_FLAG="--reboot"
    exec sg docker -c "sh '$SCRIPT_DIR/provision-host.sh' '$HOST' --stage2 $REBOOT_FLAG"
  fi
fi

echo "==> Tailscale"
if tailscale status --json 2> /dev/null | grep -q '"BackendState": "Running"'; then
  echo "Already connected."
else
  set -- --hostname="$HOST" --advertise-tags=tag:centcom --advertise-exit-node
  key="$(env_value TS_AUTHKEY)"
  if [ -n "$key" ]; then
    set -- "$@" --auth-key="$key"
  else
    echo "Open the login URL below to authenticate this node."
  fi
  sudo tailscale up "$@"
fi
TS_NAME="$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].split(".")[0])')"
if [ "$TS_NAME" != "$HOST" ]; then
  echo "WARNING: joined the tailnet as '$TS_NAME', not '$HOST'. A stale '$HOST' machine still exists;" >&2
  echo "delete it in the Tailscale admin console, then run: sudo tailscale set --hostname=$HOST" >&2
fi

echo "==> Routing"
subnet="$(env_value LAN_SUBNET)"
sh scripts/configure-routing.sh "${subnet:-192.168.4.0/22}"

echo "==> Tailscale IP in .env"
KEY="TAILSCALE_IP_$(printf '%s' "$HOST" | tr 'a-z-' 'A-Z_')"
IP="$(tailscale ip -4 | head -n 1)"
OLD_IP="$(env_value "$KEY")"
if [ "$OLD_IP" = "$IP" ]; then
  echo "$KEY=$IP is current."
else
  if grep -q "^$KEY=" .env; then
    sed -i "s|^$KEY=.*|$KEY=$IP|" .env
  else
    echo "$KEY=$IP" >> .env
  fi
  echo "$KEY changed: ${OLD_IP:-unset} -> $IP"
fi

echo "==> Hardening"
./scripts/harden-host.sh

echo "==> Stack"
docker network inspect caddy_net > /dev/null 2>&1 || docker network create caddy_net > /dev/null
./scripts/setup-cron.sh
./scripts/reload.sh

echo "==> $HOST provisioned at $IP"
if [ "$REBOOT" = true ]; then
  echo "Rebooting in 5 seconds."
  sudo systemd-run --on-active=5 systemctl reboot > /dev/null
else
  echo "Reboot when convenient so log2ram takes over /var/log."
fi
