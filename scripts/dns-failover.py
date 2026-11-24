#!/usr/bin/env python3
"""Keep the tailnet's global nameservers in step with Pi-hole health.

Run every minute on a host that is not itself a Pi-hole (see
install-dns-failover.sh). Each run:

1. Sends a real DNS query to every Pi-hole's Tailscale IP (UDP 53).
2. Applies hysteresis: a host is marked down after DOWN_AFTER consecutive failed
   runs and up again after UP_AFTER consecutive good runs.
3. Reconciles Tailscale DNS: only healthy Pi-holes are listed (in services.toml
   order) and "Override DNS servers" is ON. If none are healthy, override is turned
   OFF so clients fall back to their local resolvers; the nameserver list is left
   as it was so recovery is a single write.
4. Emails (SMTP) on every host state change, override change, and on repeated
   Tailscale API failures. Nothing is sent while the state is steady.

Safety: nothing is judged or changed unless this host's own tailscaled is Running,
so a checker that lost its own connectivity cannot turn DNS off tailnet-wide.
`touch $STATE/paused` suspends all changes (probes and state still update).

    dns-failover.py             one run
    dns-failover.py --dry-run   probe and report; no state, API writes or email

Credentials (.env): TS_DNS_OAUTH_CLIENT_ID / TS_DNS_OAUTH_CLIENT_SECRET (OAuth client
with the `dns` scope; falls back to TS_OAUTH_* / TS_API_KEY), SMTP_HOST, SMTP_PORT,
SMTP_USERNAME, SMTP_PASSWORD, DNS_ALERT_FROM, DNS_ALERT_TO (comma separated).
Optional: DNS_PROBE_NAME (default cloudflare.com), DNS_DOWN_AFTER (3), DNS_UP_AFTER (2).
"""
import argparse
import datetime
import email.message
import importlib.util
import json
import os
import random
import smtplib
import socket
import struct
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load("tailscale_policy", "tailscale-policy.py")
sync = load("sync_tailscale_dns", "sync-tailscale-dns.py")

API_FAILURES_BEFORE_ALERT = 3


# --- Probe -----------------------------------------------------------------

def dns_query(ip, name, timeout=2.0):
    ident = random.randrange(65536)
    question = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".")) + b"\0"
    packet = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0) + question + struct.pack(">HH", 1, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (ip, 53))
            data = sock.recv(512)
        except OSError:
            return False
    if len(data) < 12:
        return False
    rid, flags = struct.unpack(">HH", data[:4])
    return rid == ident and bool(flags & 0x8000) and (flags & 0xF) == 0


def probe(ip, name):
    return dns_query(ip, name) or dns_query(ip, name)


def local_tailscale_running():
    try:
        out = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        return json.loads(out.stdout).get("BackendState") == "Running"
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


# --- Decision logic (pure) -------------------------------------------------

def observe(state, host, ok, down_after, up_after):
    """Update state[host]; return 'down' / 'up' on a confirmed transition, else None."""
    entry = state.get(host)
    if entry is None:
        state[host] = {"up": ok, "streak": 0}
        return None
    if ok == entry["up"]:
        entry["streak"] = 0
        return None
    entry["streak"] += 1
    if entry["streak"] >= (up_after if ok else down_after):
        entry["up"], entry["streak"] = ok, 0
        return "up" if ok else "down"
    return None


def desired(live_addresses, managed, up_by_ip):
    """Return (nameserver address order, override) for the given health."""
    healthy = [ip for ip in managed if up_by_ip.get(ip)]
    if not healthy:
        return list(live_addresses), False
    return healthy + [a for a in live_addresses if a not in managed], True


def plan(dns, managed, up_by_ip):
    """Return (new config or None when nothing changes, list of change descriptions)."""
    live = [ns["address"] for ns in dns.get("nameservers", [])]
    addresses, override = desired(live, managed, up_by_ip)
    changes = []
    if addresses != live:
        changes.append(f"nameservers {', '.join(live) or '(none)'} -> {', '.join(addresses) or '(none)'}")
    current = bool(dns.get("preferences", {}).get("overrideLocalDNS"))
    if override != current:
        changes.append(f"Override DNS servers {'ON' if current else 'OFF'} -> {'ON' if override else 'OFF'}")
    if not changes:
        return None, []
    new = json.loads(json.dumps(dns))
    existing = {ns["address"]: ns for ns in dns.get("nameservers", [])}
    new["nameservers"] = [existing.get(a, {"address": a, "useWithExitNode": True}) for a in addresses]
    new.setdefault("preferences", {})["overrideLocalDNS"] = override
    return new, changes


# --- Tailscale API ---------------------------------------------------------

def tailscale_settings():
    config = policy.settings()
    for key in ("TS_DNS_OAUTH_CLIENT_ID", "TS_DNS_OAUTH_CLIENT_SECRET"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    if config.get("TS_DNS_OAUTH_CLIENT_ID") and config.get("TS_DNS_OAUTH_CLIENT_SECRET"):
        config["TS_OAUTH_CLIENT_ID"] = config["TS_DNS_OAUTH_CLIENT_ID"]
        config["TS_OAUTH_CLIENT_SECRET"] = config["TS_DNS_OAUTH_CLIENT_SECRET"]
        config.pop("TS_API_KEY", None)
    return config


def dns_api(config):
    base = config.get("TS_API_BASE", "https://api.tailscale.com")
    tailnet = urllib.parse.quote(config.get("TS_TAILNET", "-"), safe="")
    path = f"/api/v2/tailnet/{tailnet}/dns/configuration"
    token = policy.access_token(config)

    def get():
        status, _, text = policy.request(base, path, token)
        if status != 200:
            raise RuntimeError(f"GET dns/configuration failed: HTTP {status} {text.strip()[:300]}")
        return json.loads(text)

    def post(new):
        status, _, text = policy.request(base, path, token, "POST", json.dumps(new).encode(),
                                         {"Content-Type": "application/json"})
        if status != 200:
            raise RuntimeError(f"POST dns/configuration failed: HTTP {status} {text.strip()[:300]}")
    return get, post


# --- Email (SMTP, stdlib only) ----------------------------------------------

def send_email(env, subject, body):
    sender, to = env.get("DNS_ALERT_FROM"), [a.strip() for a in env.get("DNS_ALERT_TO", "").split(",") if a.strip()]
    host, user, password = env.get("SMTP_HOST"), env.get("SMTP_USERNAME"), env.get("SMTP_PASSWORD")
    port = int(env.get("SMTP_PORT") or 587)
    if not (sender and to and host):
        print("email skipped: set DNS_ALERT_FROM, DNS_ALERT_TO, SMTP_HOST (and SMTP_USERNAME/SMTP_PASSWORD)", file=sys.stderr)
        return False
    message = email.message.EmailMessage()
    message["From"], message["To"], message["Subject"] = sender, ", ".join(to), subject
    message.set_content(body)
    try:
        if port == 465:
            client = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            client = smtplib.SMTP(host, port, timeout=20)
            client.starttls()
        with client:
            if user:
                client.login(user, password or "")
            client.send_message(message)
        return True
    except (smtplib.SMTPException, OSError) as error:
        print(f"email failed: {error}", file=sys.stderr)
    return False


# --- Main ------------------------------------------------------------------

def state_dir():
    return Path(os.environ.get("STATE_DIRECTORY") or Path.home() / ".local" / "state" / "centcom-dns-failover")


def log(message):
    print(f"{datetime.datetime.now():%F %T} {message}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    env = {**policy.settings(), **{k: v for k, v in
           (line.partition("=")[::2] for line in (ROOT / ".env").read_text().splitlines()) if k.strip()}}
    env = {k.strip(): v.strip().strip("\"'") for k, v in env.items()}
    down_after, up_after = int(env.get("DNS_DOWN_AFTER", 3)), int(env.get("DNS_UP_AFTER", 2))
    name = env.get("DNS_PROBE_NAME", "cloudflare.com")

    if not local_tailscale_running():
        log("this host's tailscaled is not Running; skipping (cannot judge the Pi-holes)")
        return 0

    directory = state_dir()
    state_file = directory / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    hosts = state.setdefault("hosts", {})
    messages = []

    managed, up_by_ip = [], {}
    for host, ip in sync.pihole_addresses():
        ok = probe(ip, name)
        transition = observe(hosts, host, ok, down_after, up_after)
        managed.append(ip)
        up_by_ip[ip] = hosts[host]["up"]
        log(f"{host} {ip}: {'ok' if ok else 'FAIL'} (state {'up' if hosts[host]['up'] else 'DOWN'}, streak {hosts[host]['streak']})")
        if transition:
            messages.append(f"{host} ({ip}) is {'reachable again' if transition == 'up' else 'DOWN'}: DNS queries for {name} "
                            f"{'succeeded' if transition == 'up' else 'failed'} for {up_after if transition == 'up' else down_after} consecutive checks.")

    paused = (directory / "paused").exists()
    try:
        get, post = dns_api(tailscale_settings())
        new, changes = plan(get(), managed, up_by_ip)
        state["api_failures"] = 0
        if new and paused:
            log("paused: would change " + "; ".join(changes))
        elif new and args.dry_run:
            log("dry-run: would change " + "; ".join(changes))
        elif new:
            post(new)
            log("changed " + "; ".join(changes))
            messages.append("Tailscale DNS updated: " + "; ".join(changes) + ".")
    except (RuntimeError, SystemExit) as error:
        state["api_failures"] = state.get("api_failures", 0) + 1
        log(f"Tailscale API problem ({state['api_failures']} in a row): {error}")
        if state["api_failures"] == API_FAILURES_BEFORE_ALERT:
            messages.append(f"Tailscale DNS could not be reconciled for {API_FAILURES_BEFORE_ALERT} consecutive checks: {error}")

    if args.dry_run:
        return 0
    if messages:
        summary = "\n".join("* " + m for m in messages)
        up = [h for h, e in hosts.items() if e["up"]]
        status = f"\n\nPi-hole state: {', '.join(f'{h}={'up' if e['up'] else 'DOWN'}' for h, e in hosts.items())}"
        send_email(env, f"[centcom] DNS {'degraded' if len(up) < len(hosts) else 'status'}: " + ("none healthy" if not up else f"{len(up)}/{len(hosts)} healthy"),
                   summary + status + f"\n\nChecked from {socket.gethostname()}.\n")
    directory.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
