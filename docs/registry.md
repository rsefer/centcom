# Hosts and services registry

Service Compose definitions inherit logging rotation and `restart: unless-stopped`
from `services/common/compose.yaml`. Its `networked-service` base also attaches
`caddy_net`; host-networked services use only `service-defaults`. These bases
are referenced with `extends`, not included as running services. Keep top-level
network and volume declarations in the consuming Compose files, since `extends`
does not import those resources. Health checks and application-specific settings
remain explicit.

The status agent probes its lightweight `/healthz` endpoint, independent of DNS,
backup and application checks. Karakeep's Chrome probes `/json/version` using
Bash's TCP support (the image has no curl/wget); Meilisearch probes `/health`.
Both dependency checks allow 60 seconds for startup. Excalidraw, OpenGym API,
Pi-hole and Vaultwarden retain their image-provided health checks. Health status
alone does not trigger Docker restarts; the existing self-heal timer restarts
containers that stay unhealthy for five minutes.

`services.toml` is the one place that lists hosts, services, where each runs,
and its published port. Commit changes to this registry, not generated files.
All output below is gitignored and generated during deployment. Run
`make generate` before using Compose directly in a fresh checkout:

- `services/caddy/generated/routes.caddy` defines the dashboard, each host's
  status endpoint, and all service subdomains. The handwritten
  `services/caddy/Caddyfile` imports it and keeps shared TLS and logging settings.
- `services/caddy/generated/<host>.env` and `single.env` give each Caddy its
  upstream addresses: container names for services on its own host, the
  owning host's Tailscale IP and published port otherwise. Host Compose files
  load their file with `env_file`; the single-host stack uses `single.env`.
  Tailscale IPs are still read from `TAILSCALE_IP_<HOST>` in `.env`.
- `services/caddy/generated/<host>.compose.yaml` supplies host port bindings
  derived from service placements, plus Caddy, status and Pi-hole DNS bindings.
  Host Compose files extend these small definitions, which in turn extend the
  handwritten service files. Images, volumes, dependencies and application
  settings remain handwritten. Set `compose` to the base directory under
  `services` when it differs from the container name (for example `freshrss`).
- `services/caddy/generated/status.env` supplies DNS resolver addresses from
  Pi-hole placement order and proxy addresses from `proxy.primary`/`standby`.
  Resolver hosts need not be the proxy hosts. `TAILSCALE_IP` remains a legacy
  fallback for the primary proxy's named address.
- `services/caddy/generated/pihole-sync.env` supplies Pi-hole sync URLs and
  password-variable references from the same placements and published ports.
  Generated files contain references, not resolved private addresses or passwords.
- `services/caddy/www/src/services.json` drives the dashboard's service cards
  and host list. It is gitignored: `make generate`, `make dashboard-build`,
  `make dashboard-test`, and `make reload` all write it, because the Caddy image
  builds the dashboard and needs it present.
- The status agent and `make check-services` read `services.toml` directly
  (mounted into the agent container), as does `make check-proxy`.
- `make dns-records [PROXY=primary|standby]` prints the A records the proxy
  needs; add `BATCH=1` for a Route 53 change batch to pass to
  `aws route53 change-resource-record-sets --change-batch file://...`.

A new container-backed service also needs its base Compose definition and a
host Compose entry extending the generated binding when it publishes a port.
Set that published port in its registry placement; no manual Caddy site block
or duplicate host port binding is needed.
`path` is only the dashboard/health-probe path. Set `proxy_path` to restrict
proxying to an exact path (for example `/mcp`); all other paths then return 404.
Omit it to proxy the entire site, including Pi-hole outside `/admin/`.
`make test` verifies generation and routing without requiring committed output.
The Caddyfile has no upstream defaults, so a missing variable fails loudly.
`make deploy` generates routes, upstreams and dashboard data on each host before
cron setup reads Compose configuration, then `scripts/reload.sh` regenerates
them before building or starting services. Boot recovery also generates them
before starting the stack. Generation errors stop these operations. Reloading
recreates Caddy and the status agent so they load changed files.
