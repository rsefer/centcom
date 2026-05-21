#!/bin/sh

set -eu

sudo apt-get update && sudo apt-get -y upgrade

# Enable IP Forwarding
# https://tailscale.com/kb/1019/subnets?tab=linux#enable-ip-forwarding
if ! grep -q '^net.ipv4.ip_forward = 1$' /etc/sysctl.d/99-tailscale.conf 2>/dev/null; then
  echo 'net.ipv4.ip_forward = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf > /dev/null
fi
if ! grep -q '^net.ipv6.conf.all.forwarding = 1$' /etc/sysctl.d/99-tailscale.conf 2>/dev/null; then
  echo 'net.ipv6.conf.all.forwarding = 1' | sudo tee -a /etc/sysctl.d/99-tailscale.conf > /dev/null
fi
sudo sysctl -p /etc/sysctl.d/99-tailscale.conf

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

sudo systemctl enable --now containerd.service docker.service

if ! getent group docker > /dev/null; then
  sudo groupadd docker
fi
if ! id -nG "$USER" | grep -qw docker; then
  sudo usermod -a -G docker "$USER"
fi

if ! docker network inspect caddy_net > /dev/null 2>&1; then
  docker network create "caddy_net"
fi

~/centcom/setup-cron.sh
