# Applications

Notes for individual applications.

## Vaultwarden

Vaultwarden runs only on southcom and is reached at `https://vault.${DOMAIN}`.
Northcom's primary Caddy proxies to it over Tailscale; southcom's standby Caddy
uses the local container. This keeps Vaultwarden available through the standby
proxy during a northcom outage after the documented
[manual failover](resilience.md#manual-proxy-failover), but there
is no data replication: a southcom outage takes Vaultwarden offline. The host
backup includes the complete `vaultwarden_data` volume; stop Vaultwarden before
restoring it with `scripts/restore.sh`. For initial account creation only, set
`VAULTWARDEN_SIGNUPS_ALLOWED=true` in `.env`, deploy, create the account, then
set it back to `false` and deploy again. Keep signups closed.

## Host monitoring (Beszel)
- The Beszel hub is available at `https://beszel.${DOMAIN}`; its data is stored in the `beszel_data` volume. In the split deployment, the hub and its local agent run on southcom, and northcom and eastcom run separate agents.
- Agents use host networking and the read-only Docker socket for host and container metrics. The southcom agent connects over a shared Unix socket. The northcom and eastcom agents listen for authenticated SSH on their Tailscale IPs, port 45876, using the hub's public key; they do not use WebSocket tokens.
- Set `BESZEL_AGENT_KEY` and `BESZEL_AGENT_TOKEN` in the shared `.env` using the values provided by the hub for its local agent. In the hub, add or rename the local system to `southcom` with Host / IP `/beszel_socket/beszel.sock`. Add `northcom` and `eastcom` systems with their respective Tailscale IPs and port `45876`. For additional hosts, run an agent on each and add a separate system.

## Excalidraw
- Excalidraw is available at `https://draw.${DOMAIN}` through Caddy.
- Drawings autosave in the browser, not in a server volume. Export `.excalidraw` files to back them up or move them between devices.

## openGym
- [openGym](https://github.com/DuarteSantos8/openGym) is available at `https://gym.${DOMAIN}`.
- `opengym-app` uses the upstream `opengym-web` image to serve the browser app and proxy `/api/` to `opengym-api:3000`. In the split deployment, the web app also publishes a port on southcom's Tailscale IP for Caddy; the API does not publish a host port.
- Deploy with `docker compose pull opengym-api opengym-app && docker compose up -d opengym-api opengym-app`, then `docker compose up -d --no-deps --force-recreate caddy` to load the new route. Recreating Caddy refreshes its file bind mount if Git replaced the Caddyfile; a reload alone can still read the old file. Initial certificate issuance can take a few minutes.
- With Tailscale connected, open the gym URL in Safari and create your profile using a passkey. The first profile on a fresh instance becomes the admin; create yours before sharing access. Sign up as a profile rather than continuing as a guest: guest data stays in the browser.
- Install the [iPhone app](https://apps.apple.com/app/id6822275400). In the signed-in browser, open Settings > Account > **Pair the mobile app** and confirm your identity. In the iPhone app, choose **Connect to my server** and enter the gym URL (not `/api`) and the pairing code within five minutes.
- Keep Tailscale connected on the iPhone for pairing and sync. Workouts can be logged offline and sync when the server is reachable again. The browser app is useful for setup and administration even if you train almost exclusively in the iPhone app.
- The API handles mobile CORS and bearer-token authentication; do not add proxy authentication or CORS headers to this route.
- Accounts, passkeys, session secrets, workouts, and uploaded media persist in the `opengym_data` Docker volume. Back up the entire volume; stop `opengym-api` while taking a consistent backup. Changing `DOMAIN` changes the passkey hostname and requires new passkeys.

### Remote MCP (optional)
- `opengym-mcp` is upstream's official MCP-over-HTTPS container (`ghcr.io/duartesantos8/opengym-mcp`). It is stateless: no data volume and no secrets, and it reads and writes through `opengym-api` using each request's own scoped token. `opengym-app` proxies `https://gym.${DOMAIN}/mcp` to it, so there is no separate hostname, Caddy route or host port.
- Enable it by setting `COMPOSE_PROFILES=mcp` (starts the container) and `MCP=on` (enables it in the app) in `.env`, then `docker compose pull && docker compose up -d`. Without both, `/mcp` answers with a JSON 503. Use `COMPOSE_PROFILES` so normal updates and boot recovery include the service.
- In the gym app, open Settings > Integrations > API tokens > New token. Tick only the categories the client may see, and "Change directly" only where it may write. The token is shown once. Revoking it stops access immediately.
- Configure a client with URL `https://gym.${DOMAIN}/mcp` and header `Authorization: Bearer ogt_...`. For Claude Code: `claude mcp add --transport http opengym https://gym.${DOMAIN}/mcp --header "Authorization: Bearer ogt_..."`.
- The gym host is reachable only over Tailscale. Hosted connectors (claude.ai, ChatGPT) and upstream's OAuth sign-in call from the vendor's servers, so they cannot reach it and are unsupported here. Use a client on a tailnet device, such as Claude Code or Claude Desktop with a local connection. Read results can be sent to the client's AI provider.
- Check `docker compose ps opengym-mcp` and `docker compose logs opengym-mcp` if a client cannot connect. To verify the route, `curl -i -X POST -H 'Content-Type: application/json' -d '{}' https://gym.${DOMAIN}/mcp` should return `401` with a JSON error asking for an `ogt_` token. `/mcp/health` is not proxied by `opengym-app` (it only forwards the exact path `/mcp`), so it redirects to the app instead.
