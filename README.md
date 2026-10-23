# CENTCOM

Central Command for my local network.

TSDProxy allows easy connection of each service to Tailscale.

## Installation
1. Boot Raspberry PI using default Raspian image
2. Install git: `sudo apt install git`
3. Clone this repository or run `git clone https://github.com/rsefer/centcom.git && cd centcom`
4. Run `./setup.sh` or [install docker manually](https://docs.docker.com/engine/install/raspberry-pi-os/)
5. [Install Tailscale on-device](https://login.tailscale.com/admin/machines/new-linux)
	- run with the following flags but NOT with --accept-routes
	- --advertise-routes=192.168.0.0/24 --advertise-exit-node
6. Generate [Tailscale API key](https://login.tailscale.com/admin/settings/keys)
7. Fill in `.env`
	- `TSDPROXY_HOSTNAME` can be obtained by running `tailscale ip -4`
8. Run `docker compose up -d`

```sh
# update all builds
./reload

# restart caddy when making caddyfile changes
docker compose restart caddy

# force boot recovery routine manually
./boot-recover.sh
```

## Power outage recovery
- `setup.sh` installs an `@reboot` cron job that runs `boot-recover.sh`.
- On boot, that script waits for Docker, recreates `caddy_net` if missing, and runs `docker compose up -d --remove-orphans`.
- Recovery logs are written to `~/centcom/boot-recover.log`.

## Host monitoring (Beszel)
- The Beszel hub is available at `https://beszel.vpn.rsefer.com`; its data is stored in the `beszel_data` volume.
- The Compose-managed agent uses host networking, a shared Unix socket, and the read-only Docker socket for host and container metrics.
- After creating the hub admin account, add a system named `centcom` using `/beszel_socket/beszel.sock` as its Host / IP. Put the generated KEY and TOKEN in `.env` as `BESZEL_AGENT_KEY` and `BESZEL_AGENT_TOKEN` before starting the agent.

# Tailscale config
- Custom Tailscale nameservers should *not* be used - it causes a troubleshooting doomloop and is not worth the time
- To avoid creating an A record for every service, add one wildcard A record in Route 53:
	- Record name: `*.vpn` when the hosted zone is `rsefer.com`, or `*` when the hosted zone is `vpn.rsefer.com`.
	- Value: the `centcom` Tailscale IPv4 address from `tailscale ip -4` (the same value as `TSDPROXY_HOSTNAME`).
	- TTL: 300 seconds is a reasonable value.
- The wildcard makes new one-level hostnames such as `uptime.vpn.rsefer.com` resolve to `centcom`. Existing exact records take precedence and can be removed after confirming the wildcard works.
- DNS only resolves the hostname; each service still needs its TSDProxy label and Caddy route. The Tailscale IP keeps the service reachable only from devices that can access the tailnet.

# Public trusted TLS (DNS challenge)
- This repo is configured for DNS-01 ACME via Route53 so `*.vpn.rsefer.com` can use publicly trusted certificates.
- Caddy is built locally from [caddy/Dockerfile](caddy/Dockerfile) to include the Route53 DNS plugin.
- Add these to `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and `ROUTE53_HOSTED_ZONE_ID` (the public Route 53 hosted zone ID for `rsefer.com`).
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
- Redeploy with `docker compose pull --ignore-buildable && docker compose build caddy && docker compose up -d`.
- Check issuance with `docker compose logs -f caddy` and look for successful certificate obtain events.
- If issuance fails after an interrupted attempt, delete any stale `_acme-challenge.*` TXT records in Route53 for `vpn.domain.com`, and any `*.vpn.domain.com`, then restart Caddy.
