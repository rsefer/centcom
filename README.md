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

# Public trusted TLS (DNS challenge)
- This repo is configured for DNS-01 ACME via Route53 so `*.vpn.rsefer.com` can use publicly trusted certificates.
- Add these to `.env`: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`.
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
- Redeploy with `docker compose pull && docker compose up -d`.
- Check issuance with `docker compose logs -f caddy` and look for successful certificate obtain events.
