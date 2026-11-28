#!/usr/bin/env python3
"""Move the proxy DNS records between the primary and standby Caddy automatically.

Called by dns-failover.py on every run when PROXY_FAILOVER=on in .env. It replaces
the manual Route 53 edit described in docs/resilience.md.

Each run it probes both proxies the way a client would: an HTTPS request to the
proxy's Tailscale IP with the dashboard's hostname, so the certificate must be valid
for it. A proxy is "down" after PROXY_DOWN_AFTER (default 3) consecutive failed runs
and "up" again after PROXY_UP_AFTER (default 10; the standby needs only 2). Then:

* primary up                    -> records point at the primary
* primary down, standby up      -> records point at the standby
* both down                     -> nothing changes (switching cannot help)

Only the dashboard record (VPN_DOMAIN) and the wildcard (*.VPN_DOMAIN) are changed,
together, in one Route 53 change batch, and only when both are simple A records at
one of the two proxy IPs. If either is missing or points anywhere else, nothing is
changed and the problem is reported. Any other record is left alone; one that points
at a proxy IP is reported as it will not fail over.

The probe checks that the proxy serves the dashboard, not each application: an
application that is down behind a working proxy is down through both proxies.
Record TTL (300 s by default) adds to the detection time; see docs/resilience.md.
"""
import importlib.util
from pathlib import Path
import socket
import ssl

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


route53 = load("route53", "route53.py")
registry = load("centcom_services", "services.py")

DEFAULT_DOWN_AFTER = 3
DEFAULT_UP_AFTER = 10
STANDBY_UP_AFTER = 2


def enabled(env):
    return env.get("PROXY_FAILOVER", "").strip().lower() in ("on", "true", "1", "yes")


def probe_proxy(ip, domain, timeout=8):
    """True when the proxy at ip serves https://domain/ with a certificate valid for it."""
    try:
        with socket.create_connection((ip, 443), timeout) as raw, \
             ssl.create_default_context().wrap_socket(raw, server_hostname=domain) as tls:
            tls.settimeout(timeout)
            tls.sendall(f"GET / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n"
                        "User-Agent: centcom-proxy-failover\r\n\r\n".encode())
            line = tls.recv(256).split(b"\r\n", 1)[0].decode("latin-1")
        status = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return False
    # Redirects and authentication challenges count as up, as everywhere else.
    return 200 <= status < 400 or status in (401, 403)


def decide(primary_up, standby_up):
    if primary_up:
        return "primary"
    return "standby" if standby_up else None


def plan(records, domain, addresses, target):
    """Return (records to UPSERT, problems) to point the managed names at the target proxy.

    addresses maps "primary"/"standby" to Tailscale IPs. Any problem with either
    managed record means no changes at all: a half-switched pair is worse than none.
    """
    managed = [domain, f"*.{domain}"]
    changes, problems = [], []
    for name in managed:
        found = [r for r in records if r["name"] == name and r["type"] == "A"]
        if not found:
            problems.append(f"no A record named {name} in the hosted zone")
        elif len(found) > 1 or found[0]["alias"] or found[0]["set_id"] or len(found[0]["values"]) != 1:
            problems.append(f"{name} is not a simple single-value A record")
        elif found[0]["values"][0] not in addresses.values():
            problems.append(f"{name} points at {found[0]['values'][0]}, which is not a proxy address")
        elif found[0]["values"][0] != addresses[target]:
            changes.append(found[0])
    return ([] if problems else changes), problems


def unmanaged_at_proxy(records, domain, addresses):
    """Records under the domain that point at a proxy but will not fail over."""
    managed = {domain, f"*.{domain}"}
    return sorted(r["name"] for r in records
                  if r["type"] == "A" and not r["alias"] and r["name"] not in managed
                  and (r["name"] == domain or r["name"].endswith("." + domain))
                  and any(v in addresses.values() for v in r["values"]))


def reconcile(env, state, *, observe, paused=False, dry_run=False, client=None, probe=probe_proxy,
              log=print):
    """One run. Returns (messages for the email, "primary"/"standby" if records changed)."""
    config = registry.load()
    domain = env.get("VPN_DOMAIN")
    hosts = {"primary": config["proxy"]["primary"], "standby": config["proxy"]["standby"]}
    addresses = {role: registry.host_ip(config, env, host) for role, host in hosts.items()}
    if not domain or not all(addresses.values()):
        raise RuntimeError("proxy failover needs VPN_DOMAIN and both proxy hosts' Tailscale IPs in .env")
    down_after = int(env.get("PROXY_DOWN_AFTER") or DEFAULT_DOWN_AFTER)
    up_after = int(env.get("PROXY_UP_AFTER") or DEFAULT_UP_AFTER)

    proxy = state.setdefault("proxy", {})
    seen = proxy.setdefault("hosts", {})
    # Assume the primary is fine until it fails repeatedly, and the standby unusable
    # until it passes repeatedly, so a single bad first probe cannot move DNS.
    seen.setdefault("primary", {"up": True, "streak": 0})
    seen.setdefault("standby", {"up": False, "streak": 0})
    messages = []
    for role, after in (("primary", up_after), ("standby", STANDBY_UP_AFTER)):
        ok = probe(addresses[role], domain)
        transition = observe(seen, role, ok, down_after, after)
        log(f"proxy {role} {hosts[role]} {addresses[role]}: {'ok' if ok else 'FAIL'} "
            f"(state {'up' if seen[role]['up'] else 'DOWN'}, streak {seen[role]['streak']})")
        if transition and not (role == "standby" and transition == "up"):
            messages.append(f"The {role} proxy ({hosts[role]}, {addresses[role]}) is "
                            f"{'healthy again' if transition == 'up' else 'DOWN'}: "
                            f"{domain} {'answered' if transition == 'up' else 'did not answer'} over HTTPS "
                            f"for {after if transition == 'up' else down_after} consecutive checks.")

    target = decide(seen["primary"]["up"], seen["standby"]["up"])
    if client is None:
        client = route53.Route53(env.get("ROUTE53_HOSTED_ZONE_ID", ""), env.get("AWS_ACCESS_KEY_ID", ""),
                                 env.get("AWS_SECRET_ACCESS_KEY", ""))
    records = client.list_records()
    for name in unmanaged_at_proxy(records, domain, addresses):
        if name not in proxy.setdefault("warned", []):
            proxy["warned"].append(name)
            messages.append(f"{name} points at a proxy address but is not managed, so it will "
                            "not fail over; only the dashboard and wildcard records are switched.")
    if target is None:
        log("no healthy proxy; leaving DNS as it is")
        return messages, None
    changes, problems = plan(records, domain, addresses, target)
    problem = "; ".join(problems)
    if problem != proxy.get("problem", ""):
        proxy["problem"] = problem
        if problem:
            messages.append(f"Proxy failover cannot change DNS: {problem}.")
    if not changes:
        return messages, None
    names = ", ".join(r["name"] for r in changes)
    summary = f"{names} -> {hosts[target]} ({addresses[target]})"
    if paused:
        log(f"paused: would change {summary}")
        return messages, None
    if dry_run:
        log(f"dry-run: would change {summary}")
        return messages, None
    client.upsert(f"centcom proxy failover to {hosts[target]}", changes, addresses[target])
    log(f"changed {summary}")
    messages.append(f"DNS now points at the {target} proxy, {hosts[target]}: {summary}. "
                    "Clients follow as their cached records expire (the record TTL).")
    return messages, target
