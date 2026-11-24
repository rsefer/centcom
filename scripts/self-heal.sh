#!/bin/sh
# Host self-healing check. centcom-self-heal.timer runs it as root every minute.
#
# A sustained outage escalates by consecutive failed minutes:
#   3   gateway down: bounce the NIC            tailscale down: restart tailscaled
#   6   gateway down: reload the USB NIC driver tailscale down: bounce NIC + restart tailscaled
#   12  save diagnostics, then reboot (max 3 attempts until 10 healthy minutes;
#       retried every 30 minutes while the outage lasts)
# "WAN down" (gateway reachable, internet not) is logged but never acted on:
# rebooting cannot fix the ISP. It also restarts containers that stay unhealthy
# and logs under-voltage/throttle flags.
#
# Events go to $STATE_DIR/events.log on disk (survives power loss, unlike log2ram).
# Fast-changing counters live in /run so the SD card is not written every minute.

set -u

STATE_DIR="${SELFHEAL_STATE_DIR:-/var/lib/centcom-selfheal}"
RUN_DIR="${SELFHEAL_RUN_DIR:-/run/centcom-selfheal}"
DRY_RUN="${SELFHEAL_DRY_RUN:-0}"
STAGE1_AT="${SELFHEAL_STAGE1_AT:-3}"
STAGE2_AT="${SELFHEAL_STAGE2_AT:-6}"
REBOOT_AT="${SELFHEAL_REBOOT_AT:-12}"
MAX_REBOOTS="${SELFHEAL_MAX_REBOOTS:-3}"
HEALTHY_RESET="${SELFHEAL_HEALTHY_RESET:-10}"
CONTAINER_AT="${SELFHEAL_CONTAINER_AT:-5}"

mkdir -p "$STATE_DIR" "$RUN_DIR"

val() { cat "$RUN_DIR/$1" 2>/dev/null || printf '%s' "${2:-0}"; }
set_val() { printf '%s' "$2" > "$RUN_DIR/$1"; }
sval() { cat "$STATE_DIR/$1" 2>/dev/null || printf 0; }
set_sval() { printf '%s' "$2" > "$STATE_DIR/$1"; }

log() {
	line="$(date '+%F %T') $*"
	printf '%s\n' "$line"
	printf '%s\n' "$line" >> "$STATE_DIR/events.log"
	if [ "$(wc -c < "$STATE_DIR/events.log" | tr -d ' ')" -gt 262144 ]; then
		tail -n 500 "$STATE_DIR/events.log" > "$STATE_DIR/events.log.tmp" &&
			mv "$STATE_DIR/events.log.tmp" "$STATE_DIR/events.log"
	fi
}

run() {
	if [ "$DRY_RUN" = 1 ]; then
		log "dry-run: $*"
		return 0
	fi
	log "action: $*"
	"$@" > /dev/null 2>&1 || log "action failed: $*"
}

ping_ok() { ping -c 2 -W 3 "$1" > /dev/null 2>&1; }
wan_ok() { ping_ok 1.1.1.1 || ping_ok 9.9.9.9; }
default_gateway() { ip -4 route show default 2> /dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "via") { print $(i + 1); exit }}'; }
default_iface() { ip -4 route show default 2> /dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit }}'; }

tailscale_online() {
	command -v tailscale > /dev/null 2>&1 || return 0
	status="$(tailscale status --json 2> /dev/null)" || return 1
	if command -v python3 > /dev/null 2>&1; then
		printf '%s' "$status" | python3 -c '
import json, sys
d = json.load(sys.stdin)
sys.exit(0 if d.get("BackendState") == "Running" and (d.get("Self") or {}).get("Online", True) else 1)'
	else
		printf '%s' "$status" | grep -q '"BackendState": *"Running"'
	fi
}

bounce_nic() {
	iface="$(default_iface)"
	iface="${iface:-eth0}"
	run ip link set "$iface" down
	sleep 2
	run ip link set "$iface" up
}

reload_nic_driver() {
	for drv in smsc95xx lan78xx; do
		if lsmod | grep -q "^$drv "; then
			run modprobe -r "$drv"
			sleep 2
			run modprobe "$drv"
			sleep 5
		fi
	done
}

capture_diagnostics() {
	file="$STATE_DIR/diag-$(date +%Y%m%d-%H%M%S).log"
	{
		echo "reason=$reason failed_minutes=$fail uptime=$(cut -d' ' -f1 /proc/uptime 2> /dev/null)s"
		for cmd in 'vcgencmd get_throttled' 'ip -br addr' 'ip route' 'tailscale status' 'docker ps' \
			'journalctl -k -b -n 150 --no-pager' 'journalctl -u tailscaled -b -n 100 --no-pager' \
			'journalctl -u NetworkManager -b -n 60 --no-pager'; do
			echo "== $cmd"
			$cmd 2>&1
		done
	} > "$file" 2>&1
	ls -1t "$STATE_DIR"/diag-*.log 2> /dev/null | tail -n +6 | while read -r old; do rm -f "$old"; done
	sync
	log "diagnostics saved to $file"
}

try_reboot() {
	attempts="$(sval reboot_attempts)"
	if [ "$attempts" -ge "$MAX_REBOOTS" ]; then
		log "reboot suppressed: $attempts consecutive attempts without recovery; needs manual attention"
		return 0
	fi
	capture_diagnostics
	[ "$DRY_RUN" = 1 ] || set_sval reboot_attempts $((attempts + 1))
	sync
	log "rebooting: $reason down for $fail min (attempt $((attempts + 1))/$MAX_REBOOTS)"
	run systemctl reboot
}

escalate() {
	if [ "$fail" -eq "$STAGE1_AT" ]; then
		log "stage 1: $reason down for $fail min"
		if [ "$reason" = gateway ]; then bounce_nic; else run systemctl restart tailscaled; fi
	elif [ "$fail" -eq "$STAGE2_AT" ]; then
		log "stage 2: $reason down for $fail min"
		if [ "$reason" = gateway ]; then
			reload_nic_driver
		else
			bounce_nic
			run systemctl restart tailscaled
		fi
	elif [ "$fail" -ge "$REBOOT_AT" ] && [ $(((fail - REBOOT_AT) % 30)) -eq 0 ]; then
		try_reboot
	fi
}

check_power() {
	command -v vcgencmd > /dev/null 2>&1 || return 0
	flags="$(vcgencmd get_throttled 2> /dev/null | cut -d= -f2)"
	[ -n "$flags" ] || return 0
	[ "$flags" = "$(val last_throttled none)" ] && return 0
	set_val last_throttled "$flags"
	[ "$flags" = 0x0 ] && return 0
	log "power: get_throttled=$flags (0x1 under-voltage now, 0x4 throttled now, 0x10000 under-voltage since boot, 0x40000 throttled since boot)"
}

heal_containers() {
	command -v docker > /dev/null 2>&1 || return 0
	unhealthy="$(docker ps --filter health=unhealthy --format '{{.Names}}' 2> /dev/null)"
	for name in $unhealthy; do
		n=$(($(val "unhealthy.$name") + 1))
		if [ "$n" -ge "$CONTAINER_AT" ]; then
			log "container $name unhealthy for $n min"
			run docker restart "$name"
			n=0
		fi
		set_val "unhealthy.$name" "$n"
	done
	for file in "$RUN_DIR"/unhealthy.*; do
		[ -e "$file" ] || continue
		printf '%s\n' "$unhealthy" | grep -qxF "${file##*/unhealthy.}" || rm -f "$file"
	done
}

check_power

gateway="$(default_gateway)"
if wan_ok; then
	if tailscale_online; then reason=ok; else reason=tailscale; fi
elif [ -n "$gateway" ] && ping_ok "$gateway"; then
	reason=wan
else
	reason=gateway
fi

previous="$(val last_reason none)"
if [ "$reason" != "$previous" ]; then
	set_val last_reason "$reason"
	{ [ "$previous" = none ] && [ "$reason" = ok ]; } || log "connectivity: $previous -> $reason"
fi

fail="$(val fail_count)"
case "$reason" in
	gateway | tailscale)
		fail=$((fail + 1))
		set_val fail_count "$fail"
		set_val healthy_count 0
		escalate
		;;
	ok)
		[ "$fail" -gt 0 ] && log "recovered after $fail failed minutes"
		set_val fail_count 0
		healthy=$(($(val healthy_count) + 1))
		set_val healthy_count "$healthy"
		if [ "$healthy" -ge "$HEALTHY_RESET" ] && [ "$(sval reboot_attempts)" -ne 0 ]; then
			set_sval reboot_attempts 0
			log "healthy for $healthy min; reboot attempt counter reset"
		fi
		;;
esac

heal_containers
exit 0
