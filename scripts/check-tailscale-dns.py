#!/usr/bin/env python3
"""Read-only check of the tailnet's DNS settings against this repo's host IPs.

Reads GET /api/v2/tailnet/-/dns/configuration and reports:

* nameservers that match no TAILSCALE_IP_<HOST> in .env (stale after a rebuild)  -> problem
* "Override DNS servers" (overrideLocalDNS) or MagicDNS turned off                -> problem
* hosts from .env that are not listed as nameservers                              -> note
* split DNS routes and search domains (informational)

    check-tailscale-dns.py                      read-only check
    check-tailscale-dns.py --override on|off    set "Override DNS servers" (asks first; --yes skips)

The write reads the live configuration, changes only preferences.overrideLocalDNS,
POSTs the whole configuration back (the API replaces it wholesale), then re-reads it.

Credentials are the same as tailscale-policy.py (TS_API_KEY or TS_OAUTH_*).
Exits 1 on any problem.
"""
import argparse
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("tailscale_policy", ROOT / "scripts" / "tailscale-policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


def set_override(base, tailnet, token, dns, enabled, assume_yes):
    current = bool(dns.get("preferences", {}).get("overrideLocalDNS"))
    if current == enabled:
        print(f"Override DNS servers is already {'ON' if enabled else 'OFF'}; nothing to do.")
        return
    if not assume_yes:
        if not sys.stdin.isatty():
            raise SystemExit("Refusing to change DNS without a terminal; pass --yes to confirm.")
        word = "ON" if enabled else "OFF"
        if input(f"Turn Override DNS servers {word} for the whole tailnet? [y/N] ").strip().lower() != "y":
            raise SystemExit("Aborted.")
    new = json.loads(json.dumps(dns))
    new.setdefault("preferences", {})["overrideLocalDNS"] = enabled
    path = f"/api/v2/tailnet/{tailnet}/dns/configuration"
    status, _, text = policy.request(base, path, token, "POST", json.dumps(new).encode(),
                                     {"Content-Type": "application/json"})
    if status != 200:
        raise SystemExit(f"Update failed: {policy.describe(status, text)}")
    status, _, text = policy.request(base, path, token)
    if status != 200 or bool(json.loads(text).get("preferences", {}).get("overrideLocalDNS")) != enabled:
        raise SystemExit("Update was accepted but the re-read does not show the new value.")
    print(f"Override DNS servers is now {'ON' if enabled else 'OFF'}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--override", choices=("on", "off"))
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    config = policy.settings()
    known = {}
    for line in (ROOT / ".env").read_text().splitlines():
        key, _, value = line.partition("=")
        if key.startswith("TAILSCALE_IP_") and value.strip():
            known[value.strip()] = key.removeprefix("TAILSCALE_IP_").lower().replace("_", "-")

    base = config.get("TS_API_BASE", "https://api.tailscale.com")
    tailnet = urllib.parse.quote(config.get("TS_TAILNET", "-"), safe="")
    token = policy.access_token(config)
    status, _, text = policy.request(base, f"/api/v2/tailnet/{tailnet}/dns/configuration", token)
    if status != 200:
        raise SystemExit(f"DNS configuration request failed: {policy.describe(status, text)}")
    dns = json.loads(text)
    if args.override:
        set_override(base, tailnet, token, dns, args.override == "on", args.yes)
        return

    problems, notes = [], []
    prefs = dns.get("preferences", {})
    if not prefs.get("overrideLocalDNS"):
        problems.append("Override DNS servers is OFF; clients keep their local resolvers")
    if not prefs.get("magicDNS"):
        problems.append("MagicDNS is OFF")

    listed = []
    for ns in dns.get("nameservers", []):
        addr = ns["address"]
        listed.append(addr)
        name = known.get(addr)
        print(f"nameserver {addr:<16} {name or 'UNKNOWN'}" + ("" if ns.get("useWithExitNode") else "  (not used with exit nodes)"))
        if not name:
            problems.append(f"nameserver {addr} matches no host in .env (stale?)")
    if not listed:
        problems.append("no global nameservers configured")
    for addr, name in known.items():
        if addr not in listed:
            notes.append(f"{name} ({addr}) is not a global nameserver")

    print(f"overrideLocalDNS={prefs.get('overrideLocalDNS')} magicDNS={prefs.get('magicDNS')}")
    for domain, servers in (dns.get("splitDNS") or {}).items():
        print(f"split DNS {domain} -> {', '.join(servers) if servers else '(default)'}")
    if dns.get("searchPaths"):
        print("search domains:", ", ".join(dns["searchPaths"]))
    for n in notes:
        print("note:", n)
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    if not problems:
        print("Tailscale DNS settings look consistent.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
