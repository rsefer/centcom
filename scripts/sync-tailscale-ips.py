#!/usr/bin/env python3
"""Sync TAILSCALE_IP_<HOST> in every host's .env from `tailscale status`.

Dry run by default; pass --apply to write. Updates the local .env and
~/centcom/.env on each host that has a docker-compose.<host>.yml. Only keys
Keys for hosts that have a compose file but no line yet are appended. Pass
--deploy (implies --apply) to run `make deploy HOST=<h>` for changed hosts.

Usage: scripts/sync-tailscale-ips.py [--apply] [--deploy] [--user rsefer]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def tailscale_ips():
    out = subprocess.run(["tailscale", "status", "--json"], capture_output=True,
                         text=True, check=True).stdout
    data = json.loads(out)
    nodes = list(data.get("Peer", {}).values()) + [data["Self"]]
    ips = {}
    for n in sorted(nodes, key=lambda n: bool(n.get("Online"))):  # online wins
        v4 = next((a for a in n.get("TailscaleIPs", []) if "." in a), None)
        if v4:
            ips[n["HostName"].lower()] = v4
    return ips


def hosts():
    return sorted(re.fullmatch(r"docker-compose\.([a-z0-9-]+)\.yml", p.name).group(1)
                  for p in ROOT.glob("docker-compose.*.yml")
                  if re.fullmatch(r"docker-compose\.([a-z0-9-]+)\.yml", p.name))


def rewrite(text, wanted):
    changes = []

    def sub(m):
        key, old = m.group(1), m.group(2)
        new = wanted.get(key.removeprefix("TAILSCALE_IP_").lower().replace("_", "-"))
        if new and new != old:
            changes.append(f"{key}: {old or '(empty)'} -> {new}")
            return f"{key}={new}"
        return m.group(0)

    text = re.sub(r"^(TAILSCALE_IP_[A-Z0-9_]+)=(.*)$", sub, text, flags=re.M)
    for host, ip in wanted.items():
        key = "TAILSCALE_IP_" + host.upper().replace("-", "_")
        if not re.search(rf"^{key}=", text, flags=re.M):
            text = text.rstrip("\n") + f"\n{key}={ip}\n"
            changes.append(f"{key}: (missing) -> {ip}")
    return text, changes


def ssh(user, host, cmd, stdin=None):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                           f"{user}@{host}.local", cmd], input=stdin,
                          capture_output=True, text=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--deploy", action="store_true")
    ap.add_argument("--user", default="rsefer")
    args = ap.parse_args()
    args.apply = args.apply or args.deploy

    ips = tailscale_ips()
    names = hosts()
    wanted = {h: ips[h] for h in names if h in ips}
    for h in names:
        if h not in wanted:
            print(f"warning: {h} not in tailscale status; leaving its IP alone", file=sys.stderr)
    print("Tailscale IPs:", ", ".join(f"{h}={ip}" for h, ip in wanted.items()))

    changed = []
    targets = [("local", ROOT / ".env")] + [(h, None) for h in names]
    for name, path in targets:
        if path:
            if not path.exists():
                continue
            text = path.read_text()
        else:
            r = ssh(args.user, name, "cat ~/centcom/.env")
            if r.returncode:
                print(f"{name}: SKIPPED ({r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'ssh failed'})")
                continue
            text = r.stdout
        new, changes = rewrite(text, wanted)
        if not changes:
            print(f"{name}: up to date")
            continue
        print(f"{name}: " + "; ".join(changes))
        changed.append(name)
        if args.apply:
            if path:
                path.write_text(new)
            else:
                r = ssh(args.user, name, "cat > ~/centcom/.env", stdin=new)
                if r.returncode:
                    print(f"{name}: WRITE FAILED {r.stderr.strip()}", file=sys.stderr)
                    sys.exit(1)

    remote = [h for h in changed if h != "local"]
    if not changed:
        print("Nothing to change.")
    elif not args.apply:
        print("Dry run. Re-run with --apply to write.")
    elif remote:
        if args.deploy:
            for h in remote:
                print(f"==> make deploy HOST={h}")
                if subprocess.run(["make", "deploy", f"HOST={h}"], cwd=ROOT).returncode:
                    print(f"deploy failed for {h}", file=sys.stderr)
                    sys.exit(1)
        else:
            print("Written. Redeploy: " + "; ".join(f"make deploy HOST={h}" for h in remote))


if __name__ == "__main__":
    main()
