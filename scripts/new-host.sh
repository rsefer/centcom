#!/bin/sh
# Provisions a fresh Raspberry Pi as a centcom host from your workstation.
# Needs SSH access to the Pi (password or key), the shared .env in this
# checkout, and docker-compose.<host>.yml pushed to origin/main. The Pi must
# be able to `git clone` and later `git fetch` origin, as the other hosts do.
#
# Usage: ./scripts/new-host.sh <host> [address]
#   address    SSH target while the Pi is still unconfigured (default <host>.local)
#   SSH_USER   env: login user on the Pi, if not your local user
# Replacing a dead host works the same way: flash the card, boot, run this.

set -eu

HOST="${1:-}"
ADDR="${2:-}"
if [ -z "$HOST" ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 <host> [address]" >&2
  exit 1
fi
case "$HOST" in
  *[!a-z0-9-]*|-*) echo "Invalid host: $HOST" >&2; exit 1 ;;
esac

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

[ -f .env ] || { echo "Missing .env in $(pwd)." >&2; exit 1; }
[ -f "docker-compose.$HOST.yml" ] || { echo "Create docker-compose.$HOST.yml and list $HOST in services.toml first." >&2; exit 1; }
git fetch --quiet origin main
git cat-file -e "FETCH_HEAD:docker-compose.$HOST.yml" 2> /dev/null ||
  { echo "docker-compose.$HOST.yml is not on origin/main; commit and push first." >&2; exit 1; }

KEY="TAILSCALE_IP_$(printf '%s' "$HOST" | tr 'a-z-' 'A-Z_')"
TARGET="${SSH_USER:+$SSH_USER@}${ADDR:-$HOST.local}"
ORIGIN="$(git remote get-url origin)"

CONTROL_DIR="$(mktemp -d /tmp/centcom.XXXXXX)"
SSH_OPTS="-o ControlMaster=auto -o ControlPath=$CONTROL_DIR/cm -o ControlPersist=10m -o StrictHostKeyChecking=accept-new"
trap 'ssh $SSH_OPTS -O exit "$TARGET" > /dev/null 2>&1 || true; rm -rf "$CONTROL_DIR"' EXIT

remote() {
  ssh $SSH_OPTS "$TARGET" "$@"
}

echo "==> Connecting to $TARGET"
remote true

echo "==> Repository"
# -t: sudo needs a terminal when the login user has a password.
ssh -t $SSH_OPTS "$TARGET" 'command -v git > /dev/null 2>&1 || { sudo apt-get update -qq && sudo apt-get install -y git; }'
if remote '[ -d ~/centcom/.git ]'; then
  remote 'cd ~/centcom && git pull --ff-only'
else
  remote "git clone '$ORIGIN' ~/centcom" ||
    { echo "The Pi cannot clone $ORIGIN; give it access (deploy key or credential helper) and rerun." >&2; exit 1; }
fi

echo "==> Shared .env"
scp $SSH_OPTS -q .env "$TARGET:centcom/.env"
remote 'chmod 600 ~/centcom/.env'

echo "==> Provisioning (Tailscale may ask you to open a login URL)"
ssh -t $SSH_OPTS "$TARGET" "cd ~/centcom && ./scripts/provision-host.sh '$HOST' --reboot"

echo "==> Waiting for the reboot"
ssh $SSH_OPTS -O exit "$TARGET" > /dev/null 2>&1 || true
sleep 15
tries=0
until ssh $SSH_OPTS -o ConnectTimeout=5 "$TARGET" true > /dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 60 ]; then
    echo "$HOST did not return within 10 minutes." >&2
    exit 1
  fi
  sleep 10
done

NEW_IP="$(remote 'tailscale ip -4 | head -n 1')"
OLD_IP="$(sed -n "s/^$KEY=//p" .env | tail -n 1)"
IP_CHANGED=false
if [ "$NEW_IP" != "$OLD_IP" ]; then
  IP_CHANGED=true
  echo "==> $KEY changed: ${OLD_IP:-unset} -> $NEW_IP"
  if grep -q "^$KEY=" .env; then
    tmp="$(mktemp)"
    sed "s|^$KEY=.*|$KEY=$NEW_IP|" .env > "$tmp"
    cat "$tmp" > .env
    rm -f "$tmp"
  else
    echo "$KEY=$NEW_IP" >> .env
  fi
  for compose in docker-compose.*.yml; do
    other="${compose#docker-compose.}"
    other="${other%.yml}"
    [ "$other" = "docker-compose" ] && continue
    [ "$other" = "$HOST" ] && continue
    ssh -o BatchMode=yes -o ConnectTimeout=10 "$other.local" \
      "sed -i 's|^$KEY=.*|$KEY=$NEW_IP|' ~/centcom/.env" ||
      echo "Could not update .env on $other; set $KEY=$NEW_IP there." >&2
  done
fi

echo "==> Checking DNS and service URLs from $HOST"
remote 'cd ~/centcom && python3 scripts/check-services.py' ||
  echo "check-services reported problems; see above." >&2

echo
echo "$HOST is provisioned and rebooted cleanly at $NEW_IP."
echo "Remaining by hand:"
echo "  - Approve $HOST's subnet route and exit node in the Tailscale admin console if its tag is missing."
if [ "$IP_CHANGED" = true ]; then
  echo "  - Run 'make deploy' so every host picks up the new $KEY."
  echo "  - Update $HOST's address anywhere it is configured by IP (Tailscale DNS nameservers, client DNS, Beszel hub system)."
fi
echo "  - Run 'make check-hosts' to confirm no drift."
