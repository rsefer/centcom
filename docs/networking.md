# Networking, DNS and TLS

Pi-hole redundancy, the Tailscale policy, DNS failover, DNS records and certificates.

## Pi-hole redundancy

Northcom's `pihole-sync` uses Nebula Sync to copy DNS settings, groups, clients,
adlists and allow/deny lists to eastcom hourly; changes made on eastcom may be
overwritten. Its administrator API is exposed only on eastcom's Tailscale IP,
port 18085. Set `PIHOLE_NORTHCOM_PASSWORD` to a primary Pi-hole administrator
or application password and `PIHOLE_EASTCOM_PASSWORD` to the secondary's administrator
password in the shared `.env`; keep this file private. The secondary updates
gravity after each synchronization. Both resolvers must be configured on clients
before DNS redundancy is effective; merely running a second Pi-hole does not
change client DNS settings. Test both resolvers and recovery before changing
tailnet-wide DNS.

## Tailscale policy

`tailscale/policy.hujson` tags the Pis `tag:centcom`, allows only the ports the
stack uses, and auto-approves their subnet route and exit node. Tagged devices
have key expiry disabled, so tagging is also how the servers stop expiring.

- Clients (members and tagged devices): DNS 53, Caddy 80/443. Admins only: SSH 22
  and the direct app, status and Beszel ports. Pis reach each other only on the
  upstream, status and Beszel-agent ports.
- It does not know about other tagged devices (Apple TVs, Home Assistant, ...).
  Merge it into the existing policy rather than replacing it, and keep the `tests`.

Policy updates go through the Tailscale API from your workstation (not the Pis).
Create an OAuth client with only the `policy_file` scope (admin console, Settings >
OAuth clients), put `TS_OAUTH_CLIENT_ID` and
`TS_OAUTH_CLIENT_SECRET` in `.env`, then:

- `make policy-diff` shows the live policy against the file (read-only).
- `make policy-check` runs Tailscale's validation and the `tests` block (read-only).
- `make policy-apply` validates, shows the diff, asks, then replaces the live policy.
  It sends the ETag it diffed, so a concurrent console edit makes the write fail.

`policy-apply` replaces the whole policy. On the first run, read `make policy-diff`
and merge anything you want to keep into the file.

Rollout, in this order (a node can only take a tag the policy lets you own):

1. Merge the policy in the admin console, keeping the existing allow-all grant for now.
2. For each already-enrolled Pi, tag it in the admin console (Machines > ... > Edit ACL
   tags > `tag:centcom`). `tailscale set` has no tag flag, and `tailscale up
   --advertise-tags` demands every non-default flag again. Then run
   `sh scripts/configure-routing.sh 192.168.4.0/22` on the Pi; it warns if the tag is missing.
3. Confirm each Pi shows the tag and "Expiry disabled" in the admin console, and
   `make check-hosts` and `make check-services` still pass.
4. Remove the allow-all grant, then re-check DNS and a service URL from a phone.

For a fresh Pi, authenticate with
`sudo tailscale up --advertise-tags=tag:centcom --advertise-exit-node`, then run
`scripts/configure-routing.sh` for forwarding and the subnet route. Every Pi is an
exit node; clients pick one in their Tailscale app.

## DNS failover

The same timer can also switch the proxy DNS records; see
[Automatic proxy failover](resilience.md#automatic-proxy-failover).

`scripts/dns-failover.py` runs every minute on southcom (not a Pi-hole) via the
`centcom-dns-failover` timer (`make install-dns-failover` on southcom). It sends a
real DNS query to each Pi-hole's Tailscale IP and keeps the tailnet's global
nameservers in step:

- A Pi-hole is removed after 3 consecutive failed checks and re-added after 2 good ones.
- Only healthy Pi-holes are listed (in `services.toml` order); other nameservers are kept.
- With no healthy Pi-hole, "Override DNS servers" is turned OFF (clients use their local
  resolvers) and the list is left alone; it turns ON again when one recovers.
- Email over SMTP (same relay as the Beszel hub) on host state changes, DNS changes, and 3 consecutive Tailscale API failures.
- Nothing is judged unless southcom's own `tailscaled` is Running.
- `sudo touch /var/lib/centcom-dns-failover/paused` suspends changes; `dns-failover.py --dry-run` reports only.

Setup: create a Tailscale OAuth client with the `dns` scope (Read + Write) and set
`TS_DNS_OAUTH_CLIENT_ID/SECRET`, `SMTP_HOST/PORT/USERNAME/PASSWORD`, `DNS_ALERT_FROM` and `DNS_ALERT_TO`
in southcom's `.env`. Logs: `journalctl -u centcom-dns-failover`.

`make provision` / `make new-host` install the timer automatically on any host
whose compose file has no Pi-hole (currently only southcom). Set
`DNS_FAILOVER_HOST=<host>` in `.env` to pin it to one host, so a second
non-Pi-hole host never runs a competing checker. Replacing southcom therefore
restores the timer, provided the shared `.env` carries the `TS_DNS_OAUTH_*`,
`SMTP_*` and `DNS_ALERT_*` keys.

## Tailscale DNS and the wildcard record
- Custom Tailscale nameservers can cause a troubleshooting loop if DNS is unavailable. Before enabling them, verify both Pi-hole resolvers and retain a way to restore DNS or connect by IP.
- To avoid creating an A record for every service, add one wildcard A record in Route 53:
	- Record name: `*.<subdomain>` when the hosted zone is the parent domain (for `VPN_DOMAIN=vpn.example.com` in the `example.com` zone, that is `*.vpn`), or `*` when the hosted zone is `${VPN_DOMAIN}` itself.
	- Value: the `centcom` Tailscale IPv4 address from `tailscale ip -4`.
	- TTL: 300 seconds is a reasonable value.
- The wildcard makes new one-level hostnames such as `rss.${VPN_DOMAIN}` resolve to `centcom`. Existing exact records take precedence and can be removed after confirming the wildcard works.
- DNS only resolves the hostname; Caddy routes requests to the services. In the single-host deployment they share a Docker network; in the split deployment Caddy reaches southcom over Tailscale. Caddy's HTTP/HTTPS ports and Pi-hole's DNS ports bind only to northcom's Tailscale IP. The Pi-hole web UI has no directly published port; app upstream ports are reachable directly over Tailscale.

## Public trusted TLS (DNS challenge)
- This repo is configured for DNS-01 ACME via Route53 so `*.${VPN_DOMAIN}` can use publicly trusted certificates.
- Caddy is built locally from [services/caddy/Dockerfile](../services/caddy/Dockerfile) to include the Route53 DNS plugin.
- Add these to `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and `ROUTE53_HOSTED_ZONE_ID` (the public Route 53 hosted zone ID for the zone that contains `${VPN_DOMAIN}`).
- The IAM principal should have Route53 permissions to list zones and change DNS records for your hosted zone.
- Example IAM policy (replace `YOUR_HOSTED_ZONE_ID`):

```json
{
	"Version": "2012-10-17",
	"Statement": [
		{
			"Sid": "Route53ReadForDnsChallenge",
			"Effect": "Allow",
			"Action": [
				"route53:ListHostedZones",
				"route53:ListHostedZonesByName",
				"route53:ListResourceRecordSets",
				"route53:GetChange"
			],
			"Resource": "*"
		},
		{
			"Sid": "Route53WriteChallengeRecords",
			"Effect": "Allow",
			"Action": [
				"route53:ChangeResourceRecordSets"
			],
			"Resource": "arn:aws:route53:::hostedzone/YOUR_HOSTED_ZONE_ID"
		}
	]
}
```
- Redeploy with `docker compose pull --ignore-buildable && docker compose build caddy && docker compose up -d --remove-orphans`.
- Check issuance with `docker compose logs -f caddy` and look for successful certificate obtain events.
- If issuance fails after an interrupted attempt, delete any stale `_acme-challenge.*` TXT records in Route53 for `${VPN_DOMAIN}`, and any `*.${VPN_DOMAIN}`, then restart Caddy.
