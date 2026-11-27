# Hosts

How the hosts are laid out, how to provision or replace one, and the single-host install.

## Multiple hosts

The original `docker-compose.yml` remains available for a single host. For the
split deployment, use the same repository, private `.env`, and `centcom`
project name on each host. Create a host-specific `compose.yaml` symlink:
Each host file extends the shared service definitions under `services/`.

| Host | Symlink target | Services |
| --- | --- | --- |
| northcom | `docker-compose.northcom.yml` | Caddy, Pi-hole, Pi-hole sync, Beszel agent, status agent |
| southcom | `docker-compose.southcom.yml` | Standby Caddy, Beszel, Excalidraw, FreshRSS, GoLink, openGym, Vaultwarden, status agent |
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

Container stdout/stderr
logs rotate at 10 MiB and keep three files; Caddy access logs also rotate.

With a `compose.yaml` symlink, backups append the host's short hostname to
the shared `BACKUP_S3_PREFIX` (default `centcom`), producing paths such as
`centcom/northcom`, `centcom/southcom`, and `centcom/eastcom`. Each host backs up only services in
its Compose selection. The existing S3 policy must allow both prefixes. Install cron only after the
host's `.env` is configured, using `make setup-cron`. Future hosts can use
additional Compose selections and hostnames.

## Adding or replacing a host

`make new-host HOST=<name>` turns a freshly flashed Pi into a working host from
your workstation. It is also how you replace a dead Pi (for example after SD card
corruption); a replacement needs no data restore for hosts whose state is
rebuilt from the repo (eastcom's Pi-hole is refilled by northcom's `pihole-sync`).

Before running it:

1. A new host needs `docker-compose.<name>.yml` and an entry in `hosts` in
   `services.toml`; commit and push to `main`. A replacement already has both.
2. Flash Raspberry Pi OS Lite with Raspberry Pi Imager, setting a login user,
   your SSH key (or a password) and Wi-Fi/Ethernet. The hostname does not
   matter; the script sets it.
3. When replacing a host, delete the old machine in the Tailscale admin
   console first, or the new one registers as `<name>-1`.
4. Optional: a reusable auth key tagged `tag:centcom` as `TS_AUTHKEY` in `.env`
   skips the browser login. `LAN_SUBNET` overrides the advertised subnet
   (default `192.168.4.0/22`).
5. The Pi must be able to clone and fetch this repository, as the other hosts do.

Then run `make new-host HOST=eastcom` (add `ADDR=raspberrypi.local` while the Pi
still has its default name, and `SSH_USER=<login>` if it differs from yours). It:

- clones the repo to `~/centcom` and copies your `.env` (mode 600);
- runs `scripts/provision-host.sh` on the Pi, which sets the hostname, selects
  the `compose.yaml` symlink, installs Docker and Tailscale, joins the tailnet as
  `tag:centcom` with the exit node and subnet route, records the node's Tailscale
  IP as `TAILSCALE_IP_<HOST>` in `.env`, runs `make harden`, creates `caddy_net`,
  installs cron and starts the stack, and on the DNS failover host (see
  [DNS failover](networking.md#dns-failover)) installs the `centcom-dns-failover` timer;
- reboots the Pi, waits for it, and runs `make check-services` from it. The reboot
  proves that log2ram and the `@reboot` recovery job work.

Each step is idempotent, so a failed run can simply be repeated. If the new
Tailscale IP differs from `.env`, the script updates your local `.env` and the
other hosts' `.env` files, and tells you to run `make deploy` so Caddy and
`pihole-sync` pick it up. Anything configured by IP outside the repo (Tailscale
DNS nameservers, client DNS, the Beszel hub's system entry) stays manual. To run
the on-Pi half alone, use `make provision HOST=<name> [REBOOT=1]` after copying
`.env` and cloning the repo yourself.

To advertise the same subnet and an exit node from each Pi, run
`sh scripts/configure-routing.sh 192.168.4.0/22` on each host and approve both
devices' routes and exit nodes in the Tailscale admin console. Subnet routers
can provide failover; clients select exit nodes separately.

For manual control, `./scripts/setup.sh --bootstrap-only` installs only Docker and
Tailscale on a fresh Pi, without deploying services, configuring subnet routes or
adding the single-host cron jobs. Run it as the intended login user (not root).

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
