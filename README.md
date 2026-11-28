# CENTCOM

Central Command for my local network.

Services are reached through Caddy on the host's Tailscale IP.

Operational scripts live in `scripts/`, and the FreshRSS updater lives in
`services/freshrss/`. A `Makefile` wraps them: run `make help` to list targets,
e.g. `make deploy`, `make reload`, or `make setup`.

Each application is organized under `services/` with its own `compose.yaml`.
The root `compose.single.yml` includes those files for a single-host install: select
it with `ln -s compose.single.yml compose.yaml` and the usual `docker compose`
commands manage the full stack. This uses Docker Compose v2.20+;
eastcom's host configuration requires v2.24.4+.
To operate on one application directly, run
`docker compose --env-file .env -p centcom -f services/<app>/compose.yaml up -d`;
keep the project name `centcom` so named volumes are shared with the full stack.

## Documentation

| Topic | Covers |
| --- | --- |
| [Hosts](docs/hosts.md) | The northcom/southcom/eastcom split, adding or replacing a Pi, single-host install |
| [Deploying and rolling back](docs/deploying.md) | `make deploy`, `make check-hosts`, `make rollback` |
| [Hosts and services registry](docs/registry.md) | `services.toml` and the files generated from it |
| [Dashboard](docs/dashboard.md) | The landing page, Live status and the status agents |
| [Networking, DNS and TLS](docs/networking.md) | Pi-hole sync, Tailscale policy, DNS failover, DNS records, certificates |
| [Resilience and recovery](docs/resilience.md) | Host hardening, self-healing, power outages, manual proxy failover, outage drills |
| [Backups](docs/backups.md) | What is backed up, S3 setup, restoring |
| [Applications](docs/applications.md) | Vaultwarden, Beszel, Excalidraw, openGym |

## Development checks

Run `make test` for the Python tests (Docker Compose is needed for the Compose
configuration checks) and `make dashboard-test` for the Node.js dashboard
tests. Run `npm ci --prefix services/caddy/www` once, then `make dashboard-build`
to build the dashboard.

## Common commands

Run `make help` for every target.

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
