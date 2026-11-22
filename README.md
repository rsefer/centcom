# CENTCOM

Central Command for my local network.

Services are reached through Caddy on the host's Tailscale IP.

Operational scripts live in `scripts/`, and the FreshRSS updater lives in
`services/freshrss/`. A `Makefile` wraps them: run `make help` to list targets,
e.g. `make deploy`, `make reload`, or `make setup`.

Each application is organized under `services/` with its own `compose.yaml`.
The root `docker-compose.yml` includes those files so the existing `docker compose`
commands continue to manage the full stack. This uses Docker Compose v2.20+;
eastcom's host configuration requires v2.24.4+.
To operate on one application directly, run
`docker compose --env-file .env -p centcom -f services/<app>/compose.yaml up -d`;
keep the project name `centcom` so named volumes are shared with the full stack.

## Development checks

Run `make test` for the Python tests (Docker Compose is needed for the Compose
configuration checks) and `make dashboard-test` for the Node.js dashboard
tests. Run `npm ci --prefix services/caddy/www` once, then `make dashboard-build`
to build the dashboard.

## Multiple hosts

The original `docker-compose.yml` remains available for a single host. For the
split deployment, use the same repository, private `.env`, and `centcom`
project name on each host. Create a host-specific `compose.yaml` symlink:
Each host file extends the shared service definitions under `services/`.

| Host | Symlink target | Services |
| --- | --- | --- |
| northcom | `docker-compose.northcom.yml` | Caddy, Pi-hole, Pi-hole sync, Beszel agent, status agent |
| southcom | `docker-compose.southcom.yml` | Standby Caddy, Beszel, Excalidraw, FreshRSS, GoLink, openGym, status agent |
| eastcom | `docker-compose.eastcom.yml` | Secondary Pi-hole, Beszel agent, status agent |

For example, on northcom run `ln -s docker-compose.northcom.yml compose.yaml`
from the repository root; use each host's matching target on the other hosts.
Leave `COMPOSE_FILE` unset. Set `TAILSCALE_IP_NORTHCOM`,
`TAILSCALE_IP_SOUTHCOM`, and `TAILSCALE_IP_EASTCOM` to the respective hosts'
Tailscale IPv4 addresses in the shared `.env`.
Northcom's Caddy uses southcom's app ports; southcom's standby Caddy uses the
local app containers and eastcom's Pi-hole. Both serve the same hostnames
using separate certificate storage. These app ports are reachable directly
by devices allowed to connect to southcom over Tailscale, bypassing Caddy.
Pi-hole DNS is bound to the corresponding host's Tailscale IP. Point the existing
service DNS records at northcom after verifying the split.

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

Container stdout/stderr
logs rotate at 10 MiB and keep three files; Caddy access logs also rotate.

With a `compose.yaml` symlink, backups append the host's short hostname to
the shared `BACKUP_S3_PREFIX` (default `centcom`), producing paths such as
`centcom/northcom`, `centcom/southcom`, and `centcom/eastcom`. Each host backs up only services in
its Compose selection. The existing S3 policy must allow both prefixes. Install cron only after the
host's `.env` is configured, using `make setup-cron`. Future hosts can use
additional Compose selections and hostnames.

### Deploying

Commit and push changes to GitHub, then run `make deploy` from this checkout.
It discovers host names from the published `docker-compose.<host>.yml` files,
checks every host before changing any, and deploys the same commit to each.
It requires Tailscale DNS, SSH access configured for `<host>.local`, a clean
working tree and active cron on each Pi, and a fast-forwardable `main` branch.
Local uncommitted changes are not deployed, and local commits not yet pushed
cause deployment to stop. If a host fails during deployment after preflight,
earlier hosts may already be updated; fix the problem and run it again.
`make deploy HOST=northcom.local` targets one SSH host; `BRANCH=name` selects
another published branch.
After deploying, the last selected host checks both configured Pi-hole
resolvers and the HTTPS dashboard and service URLs. Python 3 and outbound
tailnet access are required on that host. A failed check fails the deployment
command but does not roll back hosts already updated. HTTP redirects and
authentication challenges count as reachable, not proof of application health.
Run `make check-services` on a configured host to repeat the checks.

`make check-hosts` reports drift without changing anything. It connects to
each host as `make deploy` does and compares the checked-out commit (against
each other and `origin/main`), branch and working-tree cleanliness, the
`compose.yaml` symlink target, each container's Compose config hash against the
checked-out files, each container's image against the locally tagged image, and
registry digests for images shared across hosts. It also flags services that
are missing, stopped, or no longer in the config. A stale config hash or image
means the host needs `make reload`. Locally built images (Caddy, openGym MCP)
are not compared by digest across hosts, and hashes assume the same
`COMPOSE_PROFILES` as the `.env` on the host. The command exits non-zero when
drift is found. `make check-hosts HOST=northcom.local` checks one SSH host, and
skips the symlink expectation like `make deploy`.

For another com, add its host Compose file, configure its matching Tailscale
hostname and SSH access, and install the repository, `.env`, and `compose.yaml`
symlink on that Pi before deploying. The one-time migration stashes on the two
existing Pis are retained; inspect them with `git stash list` on each host.

To advertise the same subnet and an exit node from each Pi, run
`sh scripts/configure-routing.sh 192.168.4.0/22` on each host and approve both
devices' routes and exit nodes in the Tailscale admin console. Subnet routers
can provide failover; clients select exit nodes separately.

For a fresh additional Pi, `./scripts/setup.sh --bootstrap-only` installs Docker
and Tailscale without deploying services, configuring subnet routes, or adding
the single-host cron jobs. Run it as the intended login user (not as root),
then run `sudo tailscale up`, authenticate in the browser, and reconnect to
pick up Docker group membership. Service placement and backup restoration
must be configured separately before starting the stack.

## Single-host installation
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

## Hosts and services registry

`services.toml` is the one place that lists hosts, services, where each runs,
and its published port. Edit it, then run `make generate` and commit the result:

- `services/caddy/generated/<host>.env` and `single.env` give each Caddy its
  upstream addresses: container names for services on its own host, the
  owning host's Tailscale IP and published port otherwise. Host Compose files
  load their file with `env_file`; the single-host stack uses `single.env`.
  Tailscale IPs are still read from `TAILSCALE_IP_<HOST>` in `.env`.
- `services/caddy/www/src/services.json` drives the dashboard's service cards
  and host list.
- The status agent and `make check-services` read `services.toml` directly
  (mounted into the agent container), as does `make check-proxy`.
- `make dns-records [PROXY=primary|standby]` prints the A records the proxy
  needs; add `BATCH=1` for a Route 53 change batch to pass to
  `aws route53 change-resource-record-sets --change-batch file://...`.

A new proxied service also needs its Compose service, published port, and a
site block in `services/caddy/Caddyfile` (`reverse_proxy {$NAME_UPSTREAM}`, or
`import ts_upstream <machine>` for a tailnet machine). `make test` fails when
the generated files are stale or the Caddyfile and `services.toml` disagree.
The Caddyfile has no upstream defaults, so a missing variable fails loudly.
Redeploying recreates Caddy and the status agent so they load changed files.

## Dashboard
- The landing page at `https://${DOMAIN}` is a React + [Radix Themes](https://www.radix-ui.com/themes) app in `services/caddy/www`.
- Tailscale Admin, Beszel, and Live status appear in a separate Health section below the application links.
- It is built inside the Caddy image (Node build stage in `services/caddy/Dockerfile`); the Pi does not need Node installed. After changing the dashboard, run `docker compose build caddy && docker compose up -d --no-deps caddy` (or `make reload`).
- Press `⌘K` (or `Ctrl+K`) to open the service search palette. If the search narrows to one match, `Enter` opens it. Dark mode follows the OS `prefers-color-scheme`.
- Service links derive their hostnames from the page's own hostname, so no domain is hard-coded. For local development, run `npm install && npm run dev` in `services/caddy/www`.
- The dashboard's Live status link opens `/live-status`, which polls each host's status agent every 30 seconds: agent reachability, the latest successful backup, and each Pi-hole's DNS response from that host. Backups more than 48 hours old are marked overdue; missing, invalid, or future timestamps are unknown. Agent reachability does not indicate application health; use the Beszel link for host metrics. DNS checks resolve `rss.${DOMAIN}` and report whether it points to northcom or southcom.
- The Live status link also summarizes reachable agents and applications on the home page, which polls the same checks every 30 seconds. Missing application results are not included in the reachable count.
- While the dashboard or Live status page is open, the active proxy's local status agent checks its application HTTPS URLs every 30 seconds. HTTP success, redirects and authentication challenges indicate reachability only, not full application health. Failed probes and unavailable checks are reported separately from agent reachability; the external Tailscale Admin link is not probed. These checks do not send alerts when both pages are closed.
- Each agent exposes these status fields and application checks on port 18086, bound to its Tailscale IP. The endpoints are unauthenticated, like the dashboard; restrict access with tailnet policy as needed. Caddy proxies the endpoints for the dashboard. No metrics history is stored.
- Each successful backup records one latest-success timestamp and whether S3 sync was configured, in a small host-local Docker volume. The dashboard shows no backup until the next successful run after this feature is installed; an unsuccessful backup leaves the previous timestamp unchanged. A local-only backup is not an off-site backup.

## Power outage recovery
- `scripts/setup.sh` installs an `@reboot` cron job that runs `scripts/boot-recover.sh`.
- On boot, that script waits for Docker and Tailscale, recreates `caddy_net` if missing, and runs `docker compose up -d --remove-orphans`.
- Recovery logs are written to `~/centcom/boot-recover.log`.

## Manual proxy failover

The standby proxy runs on southcom but DNS normally points to northcom. Failover
is **manual**, not automatic. Southcom and its applications, eastcom's Pi-hole,
Tailscale, Route 53 access, and a working client DNS resolver remain necessary.
The dashboard cannot reach northcom's agent during an outage; the northcom DNS
check will fail until it returns. Eastcom's Pi-hole web interface is a separate
replica, not northcom's administrative session. Southcom stores and renews its
own public TLS certificates using the existing Route 53 DNS challenge credentials.

Before an outage, from a tailnet-connected client with `curl`, run
`make check-proxy DOMAIN=<domain> PROXY_IP=<southcom-tailscale-ip>`. This keeps
the original HTTPS hostnames and validates certificates while directing requests
to southcom without changing DNS. Initial certificate issuance can take several
minutes. To include the optional MCP route, set `CHECK_MCP=1` before the command.
Redirects, authentication challenges and an MCP method-not-allowed
response count as reachable, not proof of application health.

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
   DOMAIN=<domain> PROXY_IP=<northcom-tailscale-ip>`, reverse the same Route 53
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
| northcom | Primary Caddy and Pi-hole are unavailable. Eastcom can answer DNS if clients are configured to use it; switch DNS manually to use southcom's proxy. | Follow Manual proxy failover; connect to northcom by IP and inspect Docker, Tailscale, Caddy and `boot-recover.log`. |
| southcom | Beszel hub, FreshRSS, Excalidraw, GoLink and openGym are unavailable; Caddy and Pi-hole can remain available. | Connect to southcom by IP and inspect its Compose services and `boot-recover.log`. |
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

## Backups
- `scripts/backup.sh` backs up persistent data for the host's configured Compose services to a timestamped directory under `~/centcom-backups/`, prunes backup directories at least 100 days old based on their modification time, and syncs each new backup to S3. Manual runs do not reduce the retention period. `scripts/setup-cron.sh` installs a daily 3:15 AM cron job in each host's local time; logs go to `~/centcom/backup.log`. Deployments ensure the jobs are installed. Run one manually with `make backup` on the host. Missing required data or failed exports make the backup fail without uploading or pruning older backups; check the log for failures. A failed S3 sync also prevents local pruning.
- Included: the `opengym_data`, `pihole_data`, `pihole_dnsmasq`, `beszel_data`, `beszel_agent_data`, and `caddy_data` volumes; FreshRSS `data/` and `extensions/`; GoLink `/opt/golink/data`; and `.env` (stored as `env`, mode 600).
- **openGym uploaded media (progress photos in `uploads/`) is excluded** to keep backups small. Everything else — accounts, passkeys, workouts, server secrets — is included. `opengym-api` is stopped briefly during its archive for a consistent snapshot and restarted automatically.
- **Pi-hole query history and the compiled blocklist are excluded** (together they are ~290MB of regenerable data). Config is fully covered: `pihole.toml`, `dnsmasq.conf`, `hosts/`, and Pi-hole's own rolling `config_backups/` and `gravity_backups/` (config-only gravity snapshots) are included. After restoring `pihole_data`, recover the adlist/group config with `docker exec pihole cp /etc/pihole/gravity_backups/gravity.db.1 /etc/pihole/gravity.db` and rebuild the blocklist with `docker exec pihole pihole -g`.
- **FreshRSS feed items are excluded** (`users/*/db.sqlite`, `cache/`, `favicons/`). Before archiving, the script exports each user's subscriptions as OPML (`data/opml-<user>.xml`), which carries feeds, categories, and per-feed settings; `config.php` files (including login credentials) are included as-is. After restoring `freshrss_data` and starting FreshRSS, run `sh scripts/restore-freshrss-subscriptions.sh` to initialize the empty SQLite databases and import the OPML. It refuses to modify non-empty databases. Read/starred state is not preserved.
- **Beszel historical metrics are excluded**: the `system_stats` and `container_stats` tables are deleted from a pruned copy of the PocketBase database before archiving. Monitor definitions, users, alert config, and the hub's agent SSH key (`id_ed25519`) are kept, so a restored hub reconnects to agents immediately and history simply regrows. The hub is stopped briefly during its archive.
- Archives are created inside throwaway `alpine` containers. S3 sync uses `amazon/aws-cli:2.27.49` with the credentials already in `.env`; newer images may require a newer ARM CPU than the Pi 3. Nothing extra needs installing on the Pi.
- Configuration in `.env`: `BACKUP_S3_BUCKET` (bucket name; S3 sync is skipped when unset), and optionally `BACKUP_S3_PREFIX` (default `centcom`), `BACKUP_ROOT`, `BACKUP_RETENTION` (local retention in days, default 100).
- Provision the S3 side with CloudFormation: `make backup-infra BUCKET=your-bucket IAM_USER=your-route53-user` (run with **admin** AWS credentials, e.g. `AWS_PROFILE=admin`). The stack (`scripts/backup-infra.yaml`) creates a private, encrypted bucket with a lifecycle rule expiring backups after 100 days (override with `DAYS=n`), plus an IAM managed policy attached to your existing Route53 user granting `s3:PutObject`/`s3:GetObject`/`s3:ListBucket` on the `centcom/` prefix. The bucket is retained if the stack is ever deleted. Re-run to update.
- Local pruning does not delete from S3; the stack's lifecycle rule caps storage cost instead.
- Restore a single archive with `./scripts/restore.sh <archive.tar.gz>` — it maps the archive name back to its volume or host path and replaces the target's contents after confirmation. The archive is checked and extracted into a temporary Docker volume before the target is touched; the staging volume needs enough free disk space. A failure during the subsequent replacement can still leave the target incomplete. Stop the owning service first (e.g. `docker compose stop opengym-api`), then `docker compose up -d` afterwards. Test a restore occasionally; a backup you've never restored is a hope, not a backup.


## Host monitoring (Beszel)
- The Beszel hub is available at `https://beszel.vpn.rsefer.com`; its data is stored in the `beszel_data` volume. In the split deployment, the hub and its local agent run on southcom, and northcom and eastcom run separate agents.
- Agents use host networking and the read-only Docker socket for host and container metrics. The southcom agent connects over a shared Unix socket. The northcom and eastcom agents listen for authenticated SSH on their Tailscale IPs, port 45876, using the hub's public key; they do not use WebSocket tokens.
- Set `BESZEL_AGENT_KEY` and `BESZEL_AGENT_TOKEN` in the shared `.env` using the values provided by the hub for its local agent. In the hub, add or rename the local system to `southcom` with Host / IP `/beszel_socket/beszel.sock`. Add `northcom` and `eastcom` systems with their respective Tailscale IPs and port `45876`. For additional hosts, run an agent on each and add a separate system.

## Excalidraw
- Excalidraw is available at `https://draw.vpn.rsefer.com` through Caddy.
- Drawings autosave in the browser, not in a server volume. Export `.excalidraw` files to back them up or move them between devices.

## openGym
- [openGym](https://github.com/DuarteSantos8/openGym) is available at `https://gym.vpn.rsefer.com` (or `https://gym.${DOMAIN}` with your configured domain).
- `opengym-app` uses the upstream `opengym-web` image to serve the browser app and proxy `/api/` to `opengym-api:3000`. In the split deployment, the web app also publishes a port on southcom's Tailscale IP for Caddy; the API does not publish a host port.
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
- Custom Tailscale nameservers can cause a troubleshooting loop if DNS is unavailable. Before enabling them, verify both Pi-hole resolvers and retain a way to restore DNS or connect by IP.
- To avoid creating an A record for every service, add one wildcard A record in Route 53:
	- Record name: `*.vpn` when the hosted zone is `rsefer.com`, or `*` when the hosted zone is `vpn.rsefer.com`.
	- Value: the `centcom` Tailscale IPv4 address from `tailscale ip -4`.
	- TTL: 300 seconds is a reasonable value.
- The wildcard makes new one-level hostnames such as `rss.vpn.rsefer.com` resolve to `centcom`. Existing exact records take precedence and can be removed after confirming the wildcard works.
- DNS only resolves the hostname; Caddy routes requests to the services. In the single-host deployment they share a Docker network; in the split deployment Caddy reaches southcom over Tailscale. Caddy's HTTP/HTTPS ports and Pi-hole's DNS ports bind only to northcom's Tailscale IP. The Pi-hole web UI has no directly published port; southcom's FreshRSS port is reachable directly over Tailscale.

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
