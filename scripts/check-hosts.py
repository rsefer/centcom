#!/usr/bin/env python3
"""Compare deployed state across centcom hosts without changing anything.

For each host this collects the checked-out commit, working-tree cleanliness,
the compose.yaml symlink target, the Compose per-service config hashes, and
running container image IDs, then reports drift:

* hosts on different commits, or behind the published branch
* dirty working tree or unexpected branch
* compose.yaml not selecting docker-compose.<host>.yml
* containers created from an older Compose config than the checked-out files
* anything `docker compose up -d` would still change (a dry run, so stale
  images and other changes Compose itself detects are covered)
* services that are missing, stopped, or no longer in the config
* the same image reference resolving to different registry digests per host
"""

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST_FILE = re.compile(r"^docker-compose\.([a-z0-9][a-z0-9-]*)\.yml$")
SAFE_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]*$")

PLAN_VERBS = {"Recreate": "recreate", "Create": "create", "Starting": "start",
              "Stopping": "stop and remove"}

REMOTE = r"""
set -u
cd ~/centcom 2>/dev/null || { echo "error|checkout|~/centcom not found"; exit 0; }
echo "head|$(git rev-parse HEAD 2>/dev/null)"
echo "branch|$(git branch --show-current 2>/dev/null)"
echo "dirty|$(git status --porcelain --untracked-files=normal 2>/dev/null | wc -l | tr -d ' ')"
if [ -L compose.yaml ]; then echo "symlink|$(readlink compose.yaml)"; else echo "symlink|-"; fi
errors=$(mktemp)
if hashes=$(docker compose config --hash '*' 2>"$errors"); then
	echo "$hashes" | while read -r service hash; do
		[ -n "$hash" ] && echo "confighash|$service|$hash"
	done
else
	echo "error|config|$(tail -n 1 "$errors")"
fi
rm -f "$errors"
ids=$(docker ps -aq --filter label=com.docker.compose.project=centcom \
	--filter label=com.docker.compose.oneoff=False 2>/dev/null || true)
if [ -n "$ids" ]; then
	docker inspect --format 'container|{{index .Config.Labels "com.docker.compose.service"}}|{{index .Config.Labels "com.docker.compose.config-hash"}}|{{.Config.Image}}|{{.State.Status}}|{{.Name}}' $ids
	docker inspect --format '{{.Config.Image}}' $ids | sort -u | while read -r ref; do
		docker image inspect --format "image|$ref|{{.Id}}|{{range .RepoDigests}}{{.}},{{end}}" "$ref" 2>/dev/null || true
	done
fi
docker compose up -d --dry-run --pull never --no-build --remove-orphans </dev/null 2>&1 |
	awk '$1 == "Container" && ($3 == "Recreate" || $3 == "Create" || $3 == "Starting" || $3 == "Stopping") && !seen[$2]++ { print "plan|" $2 "|" $3 }'
"""


def parse(output):
    report = {"hashes": {}, "containers": [], "images": {}, "plan": [], "errors": []}
    for line in output.splitlines():
        kind, _, rest = line.partition("|")
        fields = rest.split("|")
        if kind in ("head", "branch", "symlink") and len(fields) == 1:
            report[kind] = fields[0]
        elif kind == "dirty" and len(fields) == 1 and fields[0].isdigit():
            report["dirty"] = int(fields[0])
        elif kind == "confighash" and len(fields) == 2:
            report["hashes"][fields[0]] = fields[1]
        elif kind == "container" and len(fields) == 5:
            container = dict(zip(("service", "hash", "ref", "state", "name"), fields))
            container["name"] = container["name"].lstrip("/")
            report["containers"].append(container)
        elif kind == "plan" and len(fields) == 2:
            report["plan"].append(tuple(fields))
        elif kind == "image" and len(fields) == 3:
            # Locally built images can list their own ID as a repo digest.
            digests = {d.split("@", 1)[-1] for d in fields[2].split(",") if d}
            digests.discard(fields[1])
            report["images"][fields[0]] = {"id": fields[1], "digests": digests}
        elif kind == "error":
            report["errors"].append(rest.replace("|", ": ", 1))
    return report


def analyze(reports, expected_head=None, branch="main", expected_links=None):
    """Return a list of drift findings for parsed per-host reports."""
    expected_links = expected_links or {}
    problems = []
    live = {}
    for host, report in reports.items():
        if "unreachable" in report:
            problems.append(f"{host}: unreachable ({report['unreachable']})")
            continue
        live[host] = report
        for error in report["errors"]:
            problems.append(f"{host}: {error}")
        if not report.get("head"):
            problems.append(f"{host}: could not read the checked-out commit")
        elif expected_head and report["head"] != expected_head:
            problems.append(f"{host}: at {report['head'][:7]}, "
                            f"origin/{branch} is {expected_head[:7]}")
        if report.get("branch") != branch:
            problems.append(f"{host}: on branch {report.get('branch') or '(detached)'}, "
                            f"expected {branch}")
        if report.get("dirty"):
            problems.append(f"{host}: {report['dirty']} uncommitted change(s) in the checkout")
        link = expected_links.get(host)
        if link and report.get("symlink") != link:
            problems.append(f"{host}: compose.yaml -> {report.get('symlink')}, expected {link}")

        containers = {c["service"]: c for c in report["containers"]}
        flagged = set()
        for service, wanted in sorted(report["hashes"].items()):
            container = containers.get(service)
            if container is None:
                problems.append(f"{host}: {service} is configured but has no container")
                flagged.add(service)
                continue
            if container["state"] != "running":
                problems.append(f"{host}: {service} is {container['state']}")
                flagged.add(service)
            if container["hash"] != wanted:
                problems.append(f"{host}: {service} was created from an older Compose "
                                "config (recreate it)")
                flagged.add(service)
        if report["hashes"]:
            for service in sorted(set(containers) - set(report["hashes"])):
                problems.append(f"{host}: {service} is running but not in the Compose config")
                flagged.add(service)
        names = {c["name"]: c["service"] for c in report["containers"]}
        for name, action in report["plan"]:
            if names.get(name) in flagged:
                continue
            verb = PLAN_VERBS.get(action, action.lower())
            problems.append(f"{host}: `docker compose up -d` would {verb} {name} "
                            "(for example a newer image; run make reload)")

    heads = {r["head"][:7] for r in live.values() if r.get("head")}
    if len(heads) > 1 and not expected_head:
        detail = ", ".join(f"{h} {r['head'][:7]}" for h, r in sorted(live.items())
                           if r.get("head"))
        problems.append(f"hosts are on different commits: {detail}")

    digests = {}
    for host, report in live.items():
        for ref, image in report["images"].items():
            if image["digests"]:
                digests.setdefault(ref, {})[host] = image["digests"]
    for ref, by_host in sorted(digests.items()):
        sets = list(by_host.values())
        if len(by_host) > 1 and not set.intersection(*sets):
            detail = ", ".join(f"{h} {sorted(d)[0][:19]}" for h, d in sorted(by_host.items()))
            problems.append(f"{ref} resolves to different digests: {detail}")
    return problems


def discover_hosts():
    return sorted(m.group(1) for p in ROOT.glob("docker-compose.*.yml")
                  if (m := HOST_FILE.match(p.name)))


def connect(host, target):
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    if target == "all":
        command += ["-o", f"HostName={host}", "-o", f"HostKeyAlias={host}.local", f"{host}.local"]
    else:
        command.append(host)
    return command + ["sh", "-s"]


def collect(host, target):
    try:
        result = subprocess.run(connect(host, target), input=REMOTE, capture_output=True,
                                text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"unreachable": str(error)}
    if result.returncode != 0:
        return {"unreachable": (result.stderr.strip().splitlines() or ["ssh failed"])[-1]}
    return parse(result.stdout)


def published_head(branch):
    try:
        result = subprocess.run(["git", "ls-remote", "origin", f"refs/heads/{branch}"],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = result.stdout.split()
    return fields[0] if result.returncode == 0 and fields else None


def main(argv):
    target = argv[1] if len(argv) > 1 else "all"
    branch = argv[2] if len(argv) > 2 else "main"
    if not SAFE_NAME.match(branch) or (target != "all" and not SAFE_NAME.match(target)):
        print("Invalid host or branch", file=sys.stderr)
        return 2
    hosts = discover_hosts() if target == "all" else [target]
    if not hosts:
        print("No host Compose files found.", file=sys.stderr)
        return 2
    expected = published_head(branch)
    if expected is None:
        print(f"Warning: could not read origin/{branch}; comparing hosts to each other",
              file=sys.stderr)
    reports = {}
    for host in hosts:
        print(f"Checking {host}", file=sys.stderr)
        reports[host] = collect(host, target)
    links = {h: f"docker-compose.{h}.yml" for h in hosts} if target == "all" else {}

    print(f"{'host':<12} {'commit':<8} {'branch':<8} {'compose.yaml':<28} containers")
    for host, report in reports.items():
        if "unreachable" in report:
            print(f"{host:<12} unreachable")
            continue
        running = sum(c["state"] == "running" for c in report["containers"])
        print(f"{host:<12} {report.get('head', '?')[:7]:<8} {report.get('branch', '?'):<8} "
              f"{report.get('symlink', '?'):<28} {running}/{len(report['containers'])} running")

    problems = analyze(reports, expected, branch, links)
    if not problems:
        print("No drift detected.")
        return 0
    print("\nDrift detected:")
    for problem in problems:
        print(f"  - {problem}")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
