# Resilience and recovery

Host hardening, self-healing, email alerts, power-outage recovery, automatic and manual proxy failover and outage drills.

## Host hardening

`make harden` (run on each Pi as the login user, then reboot) is idempotent and
does these things:

- Enables `systemd-timesyncd` NTP. Pis have no RTC, and a wrong clock breaks TLS
  and Tailscale.
- Relies on Raspberry Pi OS's own `40-rpi-enable-watchdog.conf` (60 s hardware
  watchdog, reboot watchdog 2 min), so a hung host resets itself. The script only
  reports it and removes the ineffective 15 s drop-in an earlier version wrote.
- Installs log2ram from the azlux repository with `SIZE=128M` (override with
  `LOG2RAM_SIZE=`). It keeps `/var/log` in RAM and flushes it hourly and at
  shutdown; a power loss loses up to an hour of host logs.
- Makes the systemd journal persistent (RAM-backed under log2ram, flushed hourly
  and at shutdown), so `journalctl -b -1` shows the previous boot after a crash.
- Installs `scripts/self-heal.sh` as `/usr/local/sbin/centcom-self-heal` with a
  systemd timer (every minute) and sets `kernel.panic=10`, `kernel.panic_on_oops=1`
  so a kernel panic reboots instead of hanging. Re-run `make harden` after
  changing the script.

log2ram does not cover Docker container logs or Pi-hole's query database, which
live under `/var/lib/docker`. Verify afterwards with `df -h /var/log`
(`log2ram` filesystem), `timedatectl`, and `systemctl show -p RuntimeWatchdogUSec` (1min).

## Self-healing

The `centcom-self-heal` timer probes the default gateway, the internet (1.1.1.1,
9.9.9.9) and Tailscale (`BackendState` Running and `Self.Online`). The hardware
watchdog only catches a hung kernel; this catches a host that is up but off the
network. After consecutive failed minutes:

| Minutes | Gateway down | Tailscale down (network fine) |
| --- | --- | --- |
| 3 | Bounce the default interface | Restart `tailscaled` |
| 6 | Reload the USB NIC driver (`smsc95xx`/`lan78xx`) | Bounce the NIC, restart `tailscaled` |
| 12 | Save diagnostics, then reboot | Same |

- Internet down with the gateway reachable is logged as `wan` and never acted on.
- At most 3 consecutive reboot attempts; the counter resets after 10 healthy
  minutes. While the outage lasts, a reboot is retried every 30 minutes.
- Containers whose healthcheck stays `unhealthy` for 5 minutes are restarted.
- Under-voltage and throttle flags (`vcgencmd get_throttled`) are logged on change.
- `/var/lib/centcom-selfheal/events.log` records every action and
  `diag-*.log` (last 5) holds the kernel, Tailscale and network state captured
  before a reboot. These live on disk, not under log2ram, so they survive a
  power cut.
- Dry run: `sudo SELFHEAL_DRY_RUN=1 /usr/local/sbin/centcom-self-heal` logs actions
  without performing them.

## Alerts

The dashboard only helps while someone has it open, so these send email too.
They use the SMTP settings the DNS failover already needs (`SMTP_*`,
`DNS_ALERT_FROM`, `DNS_ALERT_TO` in `.env`; see `scripts/alerts.py`). Without
them, nothing is sent and nothing fails.

| What | Who sends it | When |
| --- | --- | --- |
| Backup failed | `backup.sh`, on the host | Immediately, with the failed parts and the end of `backup.log` |
| Backup overdue | `alert-watch.py`, on each host | The last success in `~/centcom-backups/.last-success` is over 48 hours old |
| Vaultwarden backup overdue | `alert-watch.py`, on the Vaultwarden host | Once `VAULTWARDEN_AGE_RECIPIENT` is set, the last encrypted backup is over 48 hours old; a failed run also emails from the script |
| Self-heal gave up | `alert-watch.py`, on each host | The newest self-heal event is "reboot suppressed" (the dashboard's "Needs attention") |
| Host or application down | `alert-watch.py`, on one host (and eastcom for that host) | Still failing after 3 runs (15 minutes) |

`alert-watch.py` runs from cron every 5 minutes on every host; `setup-cron.sh`
(and so `make deploy`) installs it and it logs to `~/centcom/alert-watch.log`.
It prints nothing while all is well. An alert emails once, repeats after 24 hours
(6 for hosts and applications) while the problem lasts, and emails once more when
it clears. A message that fails to send is retried on the next run. State lives
in `~/.local/state/centcom-alerts`; delete it to reset. `python3
scripts/alert-watch.py --dry-run` shows what would be sent.

Only one host probes the others, because a dead host cannot report itself and
three hosts would send three copies. It is `DNS_FAILOVER_HOST` from `.env`, or
else the first host in `services.toml` without a Pi-hole (southcom). It checks each
host's status agent (`/healthz`) and each dashboard application's HTTPS URL, with
the same rule as the dashboard: redirects and authentication challenges count as
up. That host cannot report its own failure, so the last other host in
`services.toml` (eastcom) checks that one host's status agent and nothing else,
with the same 15-minute rule, and emails when it goes quiet. This is an alert
only: DNS failover and proxy failover run on that host and stop while it is down,
and nothing else takes them over.

Email comes from the hosts, so it cannot report a host that has lost its network
or power. Self-heal's "reboot suppressed" usually means the network is down, so
that email arrives once the connection returns.

## Power outage recovery
- `scripts/setup.sh` installs an `@reboot` cron job that runs `scripts/boot-recover.sh`.
- On boot, that script waits for Docker and Tailscale, recreates `caddy_net` if missing, and runs `docker compose up -d --remove-orphans`.
- Recovery logs are written to `~/centcom/boot-recover.log`.

## Automatic proxy failover

Optional, and off until you set `PROXY_FAILOVER=on` in southcom's `.env`. The
`centcom-dns-failover` timer (see [DNS failover](networking.md#dns-failover)) then
also decides which Caddy the DNS records point at and edits them in Route 53 itself,
doing what [Manual proxy failover](#manual-proxy-failover) describes by hand.

Every minute it makes an HTTPS request to each proxy's Tailscale IP using the
dashboard's hostname, so the certificate must be valid. A proxy is down after 3
consecutive failures (`PROXY_DOWN_AFTER`) and up again after 10 consecutive
successes (`PROXY_UP_AFTER`); the standby needs only 2.

| Primary | Standby | Records point at |
| --- | --- | --- |
| up | any | primary (a manual change back to the standby is undone) |
| down | up | standby |
| down | down | unchanged: switching cannot help |

- It changes only the dashboard record (`VPN_DOMAIN`) and the wildcard
  (`*.VPN_DOMAIN`), together in one change batch, the same two records `make
  dns-records` prints. If either is missing, is an alias or has a routing policy,
  or points at an address that is not a proxy, nothing changes and you are emailed
  once. Any other record is left alone; one that points at a proxy address is
  reported once because it will not fail over.
- The probe checks that the proxy serves the dashboard, not each application. An
  application that is down behind a working proxy is down through both proxies.
- Emails (same SMTP settings as the other alerts) report a proxy going down or
  recovering, every switch of the records, and 3 consecutive failures to reach Route 53.
- It uses the `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` that Caddy already
  uses, plus `ROUTE53_HOSTED_ZONE_ID`; the IAM policy under
  [Public trusted TLS](networking.md#public-trusted-tls-dns-challenge) already
  allows listing and changing the zone. Southcom's `.env` needs them.
- **Timing:** about 3 minutes to detect, plus the records' TTL before clients move.
  Lower the TTL of the two records to 60 seconds in Route 53 to make it about 4
  minutes in total. Failing back takes 10 minutes of health plus the TTL.
- Maintenance: `sudo touch /var/lib/centcom-dns-failover/paused` stops all changes
  (probes and state still update), as for DNS failover. Remove it to resume.
  `python3 scripts/dns-failover.py --dry-run` probes, reads the zone and reports
  what it would change without writing anything. Setting `PROXY_FAILOVER=off`
  disables it. Logs: `journalctl -u centcom-dns-failover`.
- Southcom is both the checker and the standby. If southcom is down, nothing
  switches, which is correct because the standby is gone too. If southcom loses its
  own Tailscale connection it does nothing, as for DNS failover. If only the link
  between southcom and northcom breaks, southcom sees the primary as down and
  switches even though clients could still reach it; it switches back after 10
  minutes of health.

## Manual proxy failover

The standby proxy runs on southcom but DNS normally points to northcom. Without
[automatic failover](#automatic-proxy-failover) the switch is **manual**. Southcom and its applications, eastcom's Pi-hole,
Tailscale, Route 53 access, and a working client DNS resolver remain necessary.
The dashboard cannot reach northcom's agent during an outage; the northcom DNS
check will fail until it returns. Eastcom's Pi-hole web interface is a separate
replica, not northcom's administrative session. Southcom stores and renews its
own public TLS certificates using the existing Route 53 DNS challenge credentials.

Before an outage, from a tailnet-connected client with `curl`, run
`make check-proxy VPN_DOMAIN=<domain> PROXY_IP=<southcom-tailscale-ip>`. This keeps
the original HTTPS hostnames and validates certificates while directing requests
to southcom without changing DNS. Initial certificate issuance can take several
minutes. To include probes marked `optional` in `services.toml`, set `CHECK_OPTIONAL=1`
before the command. Redirects and authentication challenges count as reachable, not proof of application health.

If northcom fails:

1. Verify southcom and eastcom are reachable and use the direct proxy check
   above. Verify the client uses eastcom for DNS; some clients do not
   immediately try their secondary resolver.
2. Run `make dns-records PROXY=standby` for the records to set (`BATCH=1`
   prints a Route 53 change batch). In the Route 53 hosted zone, change the dashboard A record (`<domain>`)
   and wildcard service A record (`*.<domain>`) from northcom's Tailscale IP
   to southcom's Tailscale IP. Also change any exact service A records that
   point to northcom: exact records take precedence over the wildcard. Leave
   unrelated records alone; check for CNAMEs or local Pi-hole overrides if a
   service still resolves elsewhere. Do not publish both proxy IPs as a
   substitute for health-aware failover.
3. Query eastcom directly with `dig @<eastcom-tailscale-ip> rss.<domain> A`,
   then verify the dashboard and applications from a tailnet client. DNS TTL,
   resolver caches and existing connections can delay recovery. If resolution
   is stale, use the direct proxy check while caches expire.
4. Once northcom is healthy, check it directly with `make check-proxy
   VPN_DOMAIN=<domain> PROXY_IP=<northcom-tailscale-ip>`, reverse the same Route 53
   changes, and verify both resolvers and service URLs with
   `make check-services`. The command reports a failed northcom resolver
   during an outage even when southcom is serving traffic.

This does not make southcom's applications redundant and does not protect
against a southcom, Tailscale, or shared network outage. Avoid restoring one
host's `caddy_data` volume onto the other while either Caddy is running.

## Non-disruptive outage drills

These are diagnostic exercises, not automatic failover tests. Run them while
the system is healthy to establish a baseline; they do not stop services or
change DNS. To test an actual outage, arrange a maintenance window separately.

| Unavailable | Expected effect | Recovery starting point |
| --- | --- | --- |
| northcom | Primary Caddy and Pi-hole are unavailable. Eastcom can answer DNS if clients are configured to use it; switch DNS manually to use southcom's proxy. Vaultwarden remains available on southcom. | Follow Manual proxy failover; connect to northcom by IP and inspect Docker, Tailscale, Caddy and `boot-recover.log`. |
| southcom | Vaultwarden, Beszel hub, FreshRSS, Excalidraw, GoLink and openGym are unavailable; northcom's Caddy and Pi-hole can remain available. | Connect to southcom by IP and inspect its Compose services and `boot-recover.log`. |
| eastcom | Secondary Pi-hole and its Beszel agent are unavailable; clients using northcom can still resolve DNS. | Connect to eastcom by IP, inspect Pi-hole and verify synchronization after recovery. |
| Tailscale | Tailnet IPs and the Caddy service URLs are unreachable from affected devices; local network access may still work. | Check `tailscale status` locally and on the host. Use a known LAN address, router DHCP lease, or a directly attached console; a Tailscale IP alone cannot bypass a Tailscale outage. |
| DNS | Service names and SSH aliases may fail even when hosts are running. | Query each Pi-hole by IP, use SSH and HTTPS by IP as below, or temporarily use a working resolver on the affected client. Restore the intended DNS configuration afterward. |

1. On a configured host, run `make check-services`. It checks each configured
   Pi-hole and the HTTPS routes. This is a baseline, not a simulation of a
   disconnected host.
2. From a tailnet-connected client with `dig`, query each resolver explicitly:
   `dig @<northcom-tailscale-ip> rss.<domain> A` and
   `dig @<eastcom-tailscale-ip> rss.<domain> A`. Both should return northcom's
   Tailscale IP during normal operation or southcom's during manual failover.
   This bypasses the client's configured DNS resolver.
3. Verify IP-based SSH without depending on name resolution:
   `ssh -o HostName=<tailscale-ip> -o HostKeyAlias=<host>.local <host>.local`.
   This preserves the existing SSH alias's user, identity and host-key checks.
   Repeat for each host; the IPs are recorded in the shared `.env`.
4. Verify HTTPS without DNS using
   `curl --resolve <domain>:443:<northcom-tailscale-ip> https://<domain>/`.
   The hostname remains in the URL so TLS certificate validation still works.
5. To rehearse a host or network outage without disrupting it, use the table
   above to identify the missing components and recovery path. Confirm that
   the LAN address or physical console and SSH credentials are available
   before an actual incident; do not assume DNS or Tailscale is working.
