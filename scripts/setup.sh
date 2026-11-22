#!/bin/sh

set -eu

BOOTSTRAP_ONLY=false
case "${1:-}" in
  "") ;;
  --bootstrap-only) BOOTSTRAP_ONLY=true ;;
  *) echo "Usage: $0 [--bootstrap-only]" >&2; exit 1 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--bootstrap-only]" >&2
  exit 1
fi

sudo apt-get update
if [ "$BOOTSTRAP_ONLY" = false ]; then
  sudo apt-get -y upgrade

  # https://tailscale.com/kb/1019/subnets?tab=linux#enable-ip-forwarding
  if ! grep -q '^net.ipv4.ip_forward = 1$' /etc/sysctl.d/99-tailscale.conf 2>/dev/null; then
    echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf > /dev/null
  fi
  if ! grep -q '^net.ipv6.conf.all.forwarding = 1$' /etc/sysctl.d/99-tailscale.conf 2>/dev/null; then
    echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf > /dev/null
  fi
  sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
fi

if [ "$BOOTSTRAP_ONLY" = false ] || ! command -v docker > /dev/null 2>&1; then
  for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do sudo apt-get -y remove "$pkg" || true; done

  # Add Docker's official GPG key:
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc

  # Add the repository to Apt sources:
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
  sudo apt-get update

  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

sudo systemctl enable --now containerd.service docker.service

if ! getent group docker > /dev/null; then
  sudo groupadd docker
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -a -G docker "$USER"
fi

if [ "$BOOTSTRAP_ONLY" = true ]; then
  if ! docker compose version > /dev/null 2>&1; then
    echo "Docker Compose is not available; install the Docker Compose plugin before continuing." >&2
    exit 1
  fi
  sudo docker info > /dev/null
  if ! command -v tailscale > /dev/null 2>&1; then
    sudo apt-get install -y ca-certificates curl
    installer="$(mktemp)"
    trap 'rm -f "$installer"' EXIT
    curl -fsSL https://tailscale.com/install.sh -o "$installer"
    sudo sh "$installer"
  fi
  echo "Bootstrap complete. Run 'sudo tailscale up --advertise-tags=tag:centcom --advertise-exit-node' to authenticate, then reconnect so Docker group membership takes effect."
  exit 0
fi

if ! sudo docker network inspect caddy_net > /dev/null 2>&1; then
  sudo docker network create "caddy_net"
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
"$SCRIPT_DIR/setup-cron.sh"
