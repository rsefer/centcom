#!/bin/sh
# Host hardening for a headless Pi: NTP, hardware watchdog check, log2ram.
# Idempotent. Run on the Pi as the login user (uses sudo). Reboot afterwards
# so log2ram takes over /var/log.

set -eu

if [ "$(id -u)" -eq 0 ]; then
  echo "Run as the login user, not root." >&2
  exit 1
fi

LOG2RAM_SIZE="${LOG2RAM_SIZE:-128M}"

# --- Time sync ---------------------------------------------------------------
# Pis have no RTC; wrong time breaks TLS, Tailscale key checks and backups.
if command -v chronyd > /dev/null 2>&1; then
  echo "chrony is installed; leaving time sync to it."
else
  sudo systemctl enable --now systemd-timesyncd.service
  sudo timedatectl set-ntp true
fi

# --- Hardware watchdog -------------------------------------------------------
# Raspberry Pi OS already ships /usr/lib/systemd/system.conf.d/40-rpi-enable-watchdog.conf
# (RuntimeWatchdogSec=1m), so systemd pets /dev/watchdog and a hung host resets.
# Remove the drop-in an earlier version of this script wrote; it sorted before
# the OS file and never took effect.
if [ -e /etc/systemd/system.conf.d/10-watchdog.conf ]; then
  sudo rm /etc/systemd/system.conf.d/10-watchdog.conf
  sudo systemctl daemon-reexec
fi

# --- log2ram -----------------------------------------------------------------
# Keeps host /var/log (journal, syslog) in RAM and flushes it hourly and at
# shutdown. Docker container logs and Pi-hole data are not under /var/log.
if ! dpkg -s log2ram > /dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl gnupg
  sudo curl -fsSL https://azlux.fr/repo.gpg -o /usr/share/keyrings/azlux-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ $(. /etc/os-release && echo "$VERSION_CODENAME") main" |
    sudo tee /etc/apt/sources.list.d/azlux.list > /dev/null
  sudo apt-get update
  sudo apt-get install -y log2ram
fi
if grep -q '^SIZE=' /etc/log2ram.conf; then
  sudo sed -i "s/^SIZE=.*/SIZE=$LOG2RAM_SIZE/" /etc/log2ram.conf
else
  echo "SIZE=$LOG2RAM_SIZE" | sudo tee -a /etc/log2ram.conf > /dev/null
fi

# --- Report ------------------------------------------------------------------
echo
echo "Time:     $(timedatectl show -p NTPSynchronized --value 2>/dev/null | sed 's/^yes$/synchronized/;s/^no$/NOT synchronized yet/')"
if [ -e /dev/watchdog ]; then
  echo "Watchdog: /dev/watchdog present, systemd timeout $(systemctl show -p RuntimeWatchdogUSec --value)"
else
  echo "Watchdog: /dev/watchdog missing; the kernel has no watchdog device on this host" >&2
fi
echo "log2ram:  SIZE=$LOG2RAM_SIZE (active after reboot; check with 'df -h /var/log')"
