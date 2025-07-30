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

## To update:
```sh
docker compose pull
docker compose stop && docker compose up -d
```
