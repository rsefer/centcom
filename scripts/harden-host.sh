#!/bin/sh
# Host hardening for a headless Pi: NTP, hardware watchdog check, log2ram,
# kernel panic reboot and the self-heal timer.
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
  sudo apt-get -o DPkg::Lock::Timeout=300 update
  sudo apt-get -o DPkg::Lock::Timeout=300 install -y ca-certificates curl gnupg
  sudo curl -fsSL https://azlux.fr/repo.gpg -o /usr/share/keyrings/azlux-archive-keyring.gpg
  echo "deb [signed-by=/usr/share/keyrings/azlux-archive-keyring.gpg] http://packages.azlux.fr/debian/ $(. /etc/os-release && echo "$VERSION_CODENAME") main" |
    sudo tee /etc/apt/sources.list.d/azlux.list > /dev/null
  sudo apt-get -o DPkg::Lock::Timeout=300 update
  sudo apt-get -o DPkg::Lock::Timeout=300 install -y log2ram
fi
if grep -q '^SIZE=' /etc/log2ram.conf; then
  sudo sed -i "s/^SIZE=.*/SIZE=$LOG2RAM_SIZE/" /etc/log2ram.conf
else
  echo "SIZE=$LOG2RAM_SIZE" | sudo tee -a /etc/log2ram.conf > /dev/null
fi

# Keep the journal across reboots. With log2ram it lives in RAM and is flushed
# hourly and at shutdown, so `journalctl -b -1` works after a clean reboot and
# shows everything up to the last flush after a crash. Without a persistent
# journal there is nothing to read after a crash-and-reboot.
sudo mkdir -p /var/log/journal /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/10-centcom-persistent.conf > /dev/null <<'EOF'
[Journal]
Storage=persistent
EOF
sudo systemctl restart systemd-journald

# --- Self-healing ------------------------------------------------------------
# scripts/self-heal.sh runs every minute as root: bounces the NIC, restarts
# tailscaled and unhealthy containers, and reboots as a last resort. It is
# installed as a root-owned copy so a writable repo can never run as root;
# re-run this script to update it.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
sudo install -m 0755 -o root -g root "$SCRIPT_DIR/self-heal.sh" /usr/local/sbin/centcom-self-heal
sudo tee /etc/systemd/system/centcom-self-heal.service > /dev/null <<'EOF'
[Unit]
Description=centcom self-heal check
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/centcom-self-heal
TimeoutStartSec=300
EOF
sudo tee /etc/systemd/system/centcom-self-heal.timer > /dev/null <<'EOF'
[Unit]
Description=Run the centcom self-heal check every minute

[Timer]
OnBootSec=3min
OnUnitInactiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF
# A kernel panic or oops reboots after 10 s instead of hanging until power is cut.
sudo tee /etc/sysctl.d/90-centcom-panic.conf > /dev/null <<'EOF'
kernel.panic = 10
kernel.panic_on_oops = 1
EOF
sudo sysctl -p /etc/sysctl.d/90-centcom-panic.conf > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now centcom-self-heal.timer

# --- Report ------------------------------------------------------------------
echo
echo "Time:     $(timedatectl show -p NTPSynchronized --value 2>/dev/null | sed 's/^yes$/synchronized/;s/^no$/NOT synchronized yet/')"
if [ -e /dev/watchdog ]; then
  echo "Watchdog: /dev/watchdog present, systemd timeout $(systemctl show -p RuntimeWatchdogUSec --value)"
else
  echo "Watchdog: /dev/watchdog missing; the kernel has no watchdog device on this host" >&2
fi
echo "Self-heal: $(systemctl is-active centcom-self-heal.timer); events in /var/lib/centcom-selfheal/events.log"
echo "log2ram:  SIZE=$LOG2RAM_SIZE (active after reboot; check with 'df -h /var/log')"
