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
```

# Tailscale config
- Custom Tailscale nameservers should *not* be used - it causes a troubleshooting doomloop and is not worth the time
- A records on the live dns zone (ie. [service].vpn.rsefer.com) should point to the `centcom` Tailscale IP
