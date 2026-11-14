# CENTCOM

Central Command for my local network.

Services are reached through Caddy on the host's Tailscale IP.

Operational scripts live in `scripts/`, and the FreshRSS updater lives in
`services/freshrss/`. A `Makefile` wraps them: run `make help` to list targets,
e.g. `make deploy`, `make reload`, or `make setup`.

Each application is organized under `services/` with its own `compose.yaml`.
The root `docker-compose.yml` includes those files so the existing `docker compose`
commands continue to manage the full stack. This uses Docker Compose v2.20+.
To operate on one application directly, run
`docker compose --env-file .env -p centcom -f services/<app>/compose.yaml up -d`;
keep the project name `centcom` so named volumes are shared with the full stack.

## Installation
1. Boot Raspberry PI using default Raspian image
2. Install git: `sudo apt install git`
3. Clone this repository or run `git clone https://github.com/rsefer/centcom.git && cd centcom`
4. Run `./scripts/setup.sh` or [install docker manually](https://docs.docker.com/engine/install/raspberry-pi-os/)
5. [Install Tailscale on-device](https://login.tailscale.com/admin/machines/new-linux)
	- run with the following flags but NOT with --accept-routes
	- --advertise-routes=192.168.0.0/24 --advertise-exit-node
6. Generate a Tailscale auth key for GoLinks from [Tailscale settings](https://login.tailscale.com/admin/settings/keys)
7. Fill in `.env`
	- `TAILSCALE_IP` is the host's Tailscale IPv4 address, available from `tailscale ip -4`
	- `GO_LINK_TS_AUTHKEY` is the GoLinks auth key
8. Run `docker compose up -d --remove-orphans` to remove any previously deployed TSDProxy container.

```sh
# update all builds
make reload

# recreate caddy after Caddyfile changes to refresh its file bind mount
docker compose up -d --no-deps --force-recreate caddy

# rebuild caddy after dashboard changes (dashboard is baked into the image)
docker compose build caddy && docker compose up -d --no-deps caddy

# force boot recovery routine manually
make boot-recover
```

## Dashboard
- The landing page at `https://${DOMAIN}` is a React + [Radix Themes](https://www.radix-ui.com/themes) app in `services/caddy/www`.
- It is built inside the Caddy image (Node build stage in `services/caddy/Dockerfile`); the Pi does not need Node installed. After changing the dashboard, run `docker compose build caddy && docker compose up -d --no-deps caddy` (or `make reload`).
- Press `⌘K` (or `Ctrl+K`) to open the service search palette. If the search narrows to one match, `Enter` opens it. Dark mode follows the OS `prefers-color-scheme`.
- Service links derive their hostnames from the page's own hostname, so no domain is hard-coded. For local development, run `npm install && npm run dev` in `services/caddy/www`.

## Power outage recovery
- `scripts/setup.sh` installs an `@reboot` cron job that runs `scripts/boot-recover.sh`.
- On boot, that script waits for Docker and Tailscale, recreates `caddy_net` if missing, and runs `docker compose up -d --remove-orphans`.
- Recovery logs are written to `~/centcom/boot-recover.log`.

## Backups
- `scripts/backup.sh` backs up all persistent data to a timestamped directory under `~/centcom-backups/`, prunes to the newest 30 backups, and syncs each new backup to S3. `scripts/setup-cron.sh` installs a daily 3:15 AM cron job; logs go to `~/centcom/backup.log`. Run one manually with `make backup`.
- Included: the `opengym_data`, `pihole_data`, `pihole_dnsmasq`, `beszel_data`, `beszel_agent_data`, and `caddy_data` volumes; FreshRSS `data/` and `extensions/`; GoLink `/opt/golink/data`; and `.env` (stored as `env`, mode 600).
- **openGym uploaded media (progress photos in `uploads/`) is excluded** to keep backups small. Everything else — accounts, passkeys, workouts, server secrets — is included. `opengym-api` is stopped briefly during its archive for a consistent snapshot and restarted automatically.
- **Pi-hole query history and the compiled blocklist are excluded** (together they are ~290MB of regenerable data). Config is fully covered: `pihole.toml`, `dnsmasq.conf`, `hosts/`, and Pi-hole's own rolling `config_backups/` and `gravity_backups/` (config-only gravity snapshots) are included. After restoring `pihole_data`, recover the adlist/group config with `docker exec pihole cp /etc/pihole/gravity_backups/gravity.db.1 /etc/pihole/gravity.db` and rebuild the blocklist with `docker exec pihole pihole -g`.
- Archives are created inside throwaway `alpine` containers, and S3 sync uses the `amazon/aws-cli` image with the credentials already in `.env` — nothing extra to install on the Pi.
- Configuration in `.env`: `BACKUP_S3_BUCKET` (bucket name; S3 sync is skipped when unset), and optionally `BACKUP_S3_PREFIX` (default `centcom`), `BACKUP_ROOT`, `BACKUP_RETENTION`.
- Provision the S3 side with CloudFormation: `make backup-infra BUCKET=your-bucket IAM_USER=your-route53-user` (run with **admin** AWS credentials, e.g. `AWS_PROFILE=admin`). The stack (`scripts/backup-infra.yaml`) creates a private, encrypted bucket with a lifecycle rule expiring backups after 45 days (override with `DAYS=n`), plus an IAM managed policy attached to your existing Route53 user granting `s3:PutObject`/`s3:GetObject`/`s3:ListBucket` on the `centcom/` prefix. The bucket is retained if the stack is ever deleted. Re-run to update.
- Local pruning does not delete from S3; the stack's lifecycle rule caps storage cost instead.
- Restore a single archive with `./scripts/restore.sh <archive.tar.gz>` — it maps the archive name back to its volume or host path and replaces the target's contents after confirmation. Stop the owning service first (e.g. `docker compose stop opengym-api`), then `docker compose up -d` afterwards. Test a restore occasionally; a backup you've never restored is a hope, not a backup.


## Host monitoring (Beszel)
- The Beszel hub is available at `https://beszel.vpn.rsefer.com`; its data is stored in the `beszel_data` volume.
- The Compose-managed agent uses host networking, a shared Unix socket, and the read-only Docker socket for host and container metrics.
- After creating the hub admin account, add a system named `centcom` using `/beszel_socket/beszel.sock` as its Host / IP. Put the generated KEY and TOKEN in `.env` as `BESZEL_AGENT_KEY` and `BESZEL_AGENT_TOKEN` before starting the agent.

## Excalidraw
- Excalidraw is available at `https://draw.vpn.rsefer.com` through Caddy.
- Drawings autosave in the browser, not in a server volume. Export `.excalidraw` files to back them up or move them between devices.

## openGym
- [openGym](https://github.com/DuarteSantos8/openGym) is available at `https://gym.vpn.rsefer.com` (or `https://gym.${DOMAIN}` with your configured domain).
- `opengym-app` uses the upstream `opengym-web` image to serve the browser app and proxy `/api/` to `opengym-api:3000`. Neither service publishes a host port; access is through Caddy on Tailscale.
- Deploy with `docker compose pull opengym-api opengym-app && docker compose up -d opengym-api opengym-app`, then `docker compose up -d --no-deps --force-recreate caddy` to load the new route. Recreating Caddy refreshes its file bind mount if Git replaced the Caddyfile; a reload alone can still read the old file. Initial certificate issuance can take a few minutes.
- With Tailscale connected, open the gym URL in Safari and create your profile using a passkey. The first profile on a fresh instance becomes the admin; create yours before sharing access. Sign up as a profile rather than continuing as a guest: guest data stays in the browser.
- Install the [iPhone app](https://apps.apple.com/app/id6822275400). In the signed-in browser, open Settings > Account > **Pair the mobile app** and confirm your identity. In the iPhone app, choose **Connect to my server** and enter the gym URL (not `/api`) and the pairing code within five minutes.
- Keep Tailscale connected on the iPhone for pairing and sync. Workouts can be logged offline and sync when the server is reachable again. The browser app is useful for setup and administration even if you train almost exclusively in the iPhone app.
- The API handles mobile CORS and bearer-token authentication; do not add proxy authentication or CORS headers to this route.
- Accounts, passkeys, session secrets, workouts, and uploaded media persist in the `opengym_data` Docker volume. Back up the entire volume; stop `opengym-api` while taking a consistent backup. Changing `DOMAIN` changes the passkey hostname and requires new passkeys.

### Remote MCP (optional)
- The `opengym-mcp` service wraps upstream's stdio MCP with [Supergateway](https://github.com/supercorp-ai/supergateway), providing Streamable HTTP at `https://gym-mcp.vpn.rsefer.com/mcp`. Clients need Tailscale connectivity and support for remote MCP with a custom authentication header; this is not an OAuth endpoint or a public cloud connector.
- It reads `opengym_data` read-only and sends workout writes through `https://gym.${DOMAIN}` using a separate paired-account token. It has no published host port. All clients share one openGym profile and can read its data and log workouts; do not distribute the access token to untrusted clients. Read results can be sent to the client's AI provider.
- Build the image: `docker compose build opengym-mcp`.
- In the signed-in gym browser app, open Settings > Account > **Pair the mobile app**. Redeem the five-minute code with `docker compose run --rm --no-deps --entrypoint node opengym-mcp mcp/scripts/pair.mjs https://gym.vpn.rsefer.com YOUR-CODE`. Store the printed token as `OPENGYM_MCP_PAIRING_TOKEN` in `.env`, never in Git or a chat. This token grants full account access. It expires after `SESSION_DAYS` (90 by default); upstream's MCP does not renew it automatically. Sign out everywhere revokes it and also unpairs phones.
- Generate a separate random client token with `openssl rand -hex 32` and store it as `OPENGYM_MCP_ACCESS_TOKEN` in `.env`. Set `OPENGYM_MCP_UID` to the same profile's Account ID if more than one profile exists. Protect `.env` with `chmod 600 .env`.
- Add `COMPOSE_PROFILES=opengym-mcp` to `.env` so normal updates and boot recovery include the service. Start it with `docker compose up -d opengym-mcp`, then refresh Caddy with `docker compose up -d --no-deps --force-recreate caddy`. Initial certificate issuance can take a few minutes.
- Configure each client with URL `https://gym-mcp.vpn.rsefer.com/mcp` and header `Authorization: Bearer YOUR-ACCESS-TOKEN`. Use the **client access token**, not the paired-account token. The gateway rejects missing or incorrect tokens with HTTP 401; only `/mcp` is exposed by Caddy.
- For VS Code, add an HTTP server to its MCP configuration using the editor's secure input prompt:

```json
{
  "inputs": [
    {
      "id": "opengym-mcp-token",
      "type": "promptString",
      "description": "openGym MCP client access token",
      "password": true
    }
  ],
  "servers": {
    "opengym": {
      "type": "http",
      "url": "https://gym-mcp.vpn.rsefer.com/mcp",
      "headers": {
        "Authorization": "Bearer ${input:opengym-mcp-token}"
      }
    }
  }
}
```

- Check `docker compose ps opengym-mcp` and `docker compose logs opengym-mcp` if a client cannot connect. The container refuses to start with either token unset. To rotate client access, update `OPENGYM_MCP_ACCESS_TOKEN`, recreate the service, and update clients. To renew workout-write access, pair again, replace `OPENGYM_MCP_PAIRING_TOKEN`, and recreate the service.
- The Dockerfile pins the upstream source revision and gateway version. Updating the API does not automatically update the MCP source; update its pin and rebuild after checking upstream compatibility.

# Tailscale config
- Custom Tailscale nameservers should *not* be used - it causes a troubleshooting doomloop and is not worth the time
- To avoid creating an A record for every service, add one wildcard A record in Route 53:
	- Record name: `*.vpn` when the hosted zone is `rsefer.com`, or `*` when the hosted zone is `vpn.rsefer.com`.
	- Value: the `centcom` Tailscale IPv4 address from `tailscale ip -4`.
	- TTL: 300 seconds is a reasonable value.
- The wildcard makes new one-level hostnames such as `rss.vpn.rsefer.com` resolve to `centcom`. Existing exact records take precedence and can be removed after confirming the wildcard works.
- DNS only resolves the hostname; Caddy routes requests to services on the private Docker network. Caddy's HTTP/HTTPS ports and Pi-hole's DNS ports bind only to the host's Tailscale IP. FreshRSS and the Pi-hole web UI have no directly published ports.

# Public trusted TLS (DNS challenge)
- This repo is configured for DNS-01 ACME via Route53 so `*.vpn.rsefer.com` can use publicly trusted certificates.
- Caddy is built locally from [services/caddy/Dockerfile](services/caddy/Dockerfile) to include the Route53 DNS plugin.
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
- Redeploy with `docker compose pull --ignore-buildable && docker compose build caddy && docker compose up -d --remove-orphans`.
- Check issuance with `docker compose logs -f caddy` and look for successful certificate obtain events.
- If issuance fails after an interrupted attempt, delete any stale `_acme-challenge.*` TXT records in Route53 for `vpn.domain.com`, and any `*.vpn.domain.com`, then restart Caddy.
