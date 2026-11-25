#!/usr/bin/env python3
"""Point the tailnet's global DNS nameservers at the hosts that run Pi-hole.

    sync-tailscale-dns.py            show the change (read-only)
    sync-tailscale-dns.py --apply    write it

Pi-hole hosts come from [services.pihole].placement in services.toml; their
Tailscale IPs come from TAILSCALE_IP_<HOST> in .env. Only the nameserver list is
replaced (existing useWithExitNode flags are kept, new entries get true); the
"Override DNS servers" toggle, MagicDNS, split DNS and search domains are left
as they are. Credentials are the same as tailscale-policy.py.
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
spec = importlib.util.spec_from_file_location("centcom_services", ROOT / "scripts" / "services.py")
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def pihole_addresses():
    config = registry.load(ROOT / "services.toml")
    env = registry.parse_env((ROOT / ".env").read_text())
    result = []
    for host in registry.pihole_hosts(config):
        key = registry.ip_var(host)
        if not env.get(key):
            raise SystemExit(f"{key} is not set in .env; cannot resolve Pi-hole host {host}")
        result.append((host, env[key]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    config = policy.settings()
    base = config.get("TS_API_BASE", "https://api.tailscale.com")
    tailnet = urllib.parse.quote(config.get("TS_TAILNET", "-"), safe="")
    token = policy.access_token(config)
    path = f"/api/v2/tailnet/{tailnet}/dns/configuration"

    status, _, text = policy.request(base, path, token)
    if status != 200:
        raise SystemExit(f"DNS configuration request failed: {policy.describe(status, text)}")
    dns = json.loads(text)

    wanted = pihole_addresses()
    old = {ns["address"]: ns.get("useWithExitNode", True) for ns in dns.get("nameservers", [])}
    new_list = [{"address": ip, "useWithExitNode": old.get(ip, True)} for _, ip in wanted]
    print("Pi-hole hosts:", ", ".join(f"{h}={ip}" for h, ip in wanted))
    print("current:", ", ".join(old) or "(none)")
    print("desired:", ", ".join(ns["address"] for ns in new_list))
    if [ns["address"] for ns in dns.get("nameservers", [])] == [ns["address"] for ns in new_list]:
        print("Nameservers already match.")
        return
    if not args.apply:
        print("Run with --apply to write it.")
        return

    new = json.loads(json.dumps(dns))
    new["nameservers"] = new_list
    status, _, text = policy.request(base, path, token, "POST", json.dumps(new).encode(),
                                     {"Content-Type": "application/json"})
    if status != 200:
        raise SystemExit(f"Update failed: {policy.describe(status, text)}")
    status, _, text = policy.request(base, path, token)
    live = [ns["address"] for ns in json.loads(text).get("nameservers", [])] if status == 200 else None
    if live != [ns["address"] for ns in new_list]:
        raise SystemExit("Update was accepted but the re-read does not show the new nameservers.")
    print("Nameservers updated.")


if __name__ == "__main__":
    main()
