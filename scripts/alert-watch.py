#!/usr/bin/env python3
"""Email when something needs attention and nobody is looking at the dashboard.

Run every 5 minutes from cron on every host (setup-cron.sh installs it).

Every host checks itself:
* backups overdue: $BACKUP_ROOT/.last-success (default ~/centcom-backups) is more
  than 48 hours old, the dashboard's threshold. A failed backup also emails at
  once from backup.sh; this catches a backup that never ran.
* self-heal gave up: the newest /var/lib/centcom-selfheal/events.log line says
  "reboot suppressed", the dashboard's "Needs attention".

One host also probes the others, so a dead host cannot report itself: the
DNS_FAILOVER_HOST from .env, else the first host in services.toml without a
Pi-hole (the host that runs dns-failover.py). It checks
* each host's status agent (http://<tailscale ip>:<status port>/healthz), and
* each dashboard application's HTTPS URL; redirects and authentication
  challenges count as up, as they do on the dashboard.

A problem must persist for 3 runs (15 minutes) before probes email, then emails
once, repeats after 6 hours (24 for backups and self-heal) while it lasts, and
emails once more when it clears. A message that fails to send is retried on
the next run. Steady state prints nothing.

    alert-watch.py             one run
    alert-watch.py --dry-run   report what would be sent; no email and no state

Needs SMTP_* and DNS_ALERT_* in .env (see alerts.py). State: ~/.local/state/centcom-alerts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


alerts = load("alerts", "alerts.py")
checks = load("check_services", "check-services.py")
registry = checks.registry

BACKUP_MAX_AGE = datetime.timedelta(hours=48)
SELFHEAL_EVENTS = Path("/var/lib/centcom-selfheal/events.log")
PROBE_CONFIRM = 3
PROBE_REMIND = 6 * 3600
LOCAL_REMIND = 24 * 3600


# --- Episode tracking (pure) -------------------------------------------------

def track(state, key, active, now, confirm=1, remind=None):
    """Update state[key]; return 'start', 'remind' or 'end' when an email is due.

    A change needs `confirm` consecutive observations. 'start' repeats on every
    run until acknowledge() records it as sent, so a failed send is retried.
    """
    entry = state.setdefault(key, {"active": False, "streak": 0, "alerted": None})
    if active != entry["active"]:
        entry["streak"] += 1
        if entry["streak"] < confirm:
            return None
        entry["active"], entry["streak"] = active, 0
    else:
        entry["streak"] = 0
    if entry["active"]:
        if entry["alerted"] is None:
            return "start"
        if remind and now - entry["alerted"] >= remind:
            return "remind"
    elif entry["alerted"] is not None:
        return "end"
    return None


def acknowledge(state, key, kind, now):
    state[key]["alerted"] = None if kind == "end" else now


# --- Local checks (pure) -----------------------------------------------------

def parse_time(text):
    return datetime.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))


def backup_overdue(marker, now, first_seen, host):
    """marker: text of .last-success or None. Return (overdue, detail)."""
    now_time = datetime.datetime.fromtimestamp(now, datetime.timezone.utc)
    try:
        last = parse_time(marker) if marker else None
    except ValueError:
        last = None
    if last is None:
        # A host that has never completed a backup is only flagged once the
        # watcher itself has been running for longer than the limit.
        since = datetime.datetime.fromtimestamp(first_seen, datetime.timezone.utc)
        detail = f"No successful backup is recorded on {host} (checked since {since:%F %T} UTC)."
        return now_time - since > BACKUP_MAX_AGE, detail
    age = now_time - last
    hours = int(age.total_seconds() // 3600)
    detail = (f"Backups are overdue on {host}: the last success was {last:%F %T} UTC "
              f"({hours} hours ago; the limit is {BACKUP_MAX_AGE.days * 24}).")
    return age > BACKUP_MAX_AGE, detail


def selfheal_attention(lines, host):
    if not lines or "reboot suppressed" not in lines[-1]:
        return False, ""
    recent = "\n".join("  " + line for line in lines[-3:])
    return True, (f"Self-heal has stopped rebooting {host} and needs manual attention. "
                  f"Latest events:\n{recent}")


# --- Probes ------------------------------------------------------------------

def probe_agent(ip, port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://{ip}:{port}/healthz", timeout=5):
            return None
    except (OSError, urllib.error.URLError, ValueError) as error:
        return str(error)


def probe_app(url):
    try:
        checks.check_url(url)
        return None
    except urllib.error.HTTPError as error:
        return f"HTTP {error.code}"
    except (OSError, urllib.error.URLError, ValueError) as error:
        return str(error)


def probe_host_name(config, values):
    """The one host that probes the others."""
    pinned = values.get("DNS_FAILOVER_HOST")
    if pinned:
        return pinned
    with_pihole = set(registry.pihole_hosts(config))
    return next((h for h in config["hosts"] if h not in with_pihole), None)


def probe_checks(config, values, host):
    """Return check dicts for host and application reachability."""
    domain, port = values.get("VPN_DOMAIN"), config["status"]["port"]
    targets = {}
    for name in config["hosts"]:
        ip = registry.host_ip(config, values, name)
        if ip:
            targets[f"host:{name}"] = (f"{name} unreachable", name, ip, port, None)
    if domain:
        for name, path in registry.applications(config).items():
            url = f"https://{name}.{domain}{path}"
            targets[f"app:{name}"] = (f"{name} down", name, None, None, url)

    def run(target):
        _, _, ip, agent_port, url = target
        return probe_app(url) if url else probe_agent(ip, agent_port)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(zip(targets, pool.map(run, targets.values())))
    rows = []
    for key, (label, name, ip, agent_port, url) in targets.items():
        error = results[key]
        if key.startswith("host:"):
            detail = f"{name}'s status agent at {ip}:{agent_port} is unreachable: {error}"
        else:
            detail = f"{name} is failing at {url}: {error}"
        rows.append({"key": key, "label": label, "active": error is not None, "detail": detail,
                     "recovered": f"{label.rsplit(' ', 1)[0]} is reachable again.",
                     "confirm": PROBE_CONFIRM, "remind": PROBE_REMIND})
    return rows


# --- Evaluation (pure) -------------------------------------------------------

def evaluate(state, rows, now):
    """Return [(key, kind, label, text)] for each email due."""
    due = []
    for row in rows:
        kind = track(state, row["key"], row["active"], now, row["confirm"], row["remind"])
        if kind:
            text = row["recovered"] if kind == "end" else row["detail"]
            due.append((row["key"], kind, row["label"], text))
    return due


def compose(host, due):
    problems = [label for _, kind, label, _ in due if kind != "end"]
    recovered = [label for _, kind, label, _ in due if kind == "end"]
    parts = []
    if problems:
        parts.append(", ".join(problems))
    if recovered:
        parts.append("recovered: " + ", ".join(recovered))
    body = "\n\n".join(f"* {text}" for _, _, _, text in due)
    return f"[centcom] {host}: " + "; ".join(parts), f"{body}\n\nChecked from {host}.\n"


# --- Main --------------------------------------------------------------------

def read_text(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def state_dir():
    return Path(os.environ.get("CENTCOM_ALERT_STATE") or Path.home() / ".local" / "state" / "centcom-alerts")


def log(message):
    print(f"{datetime.datetime.now():%F %T} {message}", flush=True)


def local_rows(env, state, now, host, selfheal_path=SELFHEAL_EVENTS):
    state.setdefault("first_seen", now)
    root = Path(env.get("BACKUP_ROOT") or Path.home() / "centcom-backups")
    overdue, backup_detail = backup_overdue(read_text(root / ".last-success"), now,
                                            state["first_seen"], host)
    rows = [{"key": "backup", "label": "backup overdue", "active": overdue,
             "detail": backup_detail, "recovered": f"Backups are current again on {host}.",
             "confirm": 1, "remind": LOCAL_REMIND}]
    events = read_text(selfheal_path)
    if events is not None:
        attention, detail = selfheal_attention(events.splitlines(), host)
        rows.append({"key": "selfheal", "label": "self-heal needs attention", "active": attention,
                     "detail": detail, "recovered": f"Self-heal no longer reports a suppressed reboot on {host}.",
                     "confirm": 1, "remind": LOCAL_REMIND})
    return rows


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    env = alerts.settings()
    host = socket.gethostname().split(".")[0]
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    directory = state_dir()
    state_file = directory / "state.json"
    try:
        state = json.loads(state_file.read_text())
    except (OSError, ValueError):
        state = {}
    tracked = state.setdefault("checks", {})

    rows = local_rows(env, state, now, host)
    try:
        config = registry.load()
        if probe_host_name(config, env) == host:
            rows += probe_checks(config, env, host)
    except (OSError, ValueError, KeyError) as error:
        log(f"services.toml: {error}")

    due = evaluate(tracked, rows, now)
    if due:
        subject, body = compose(host, due)
        if args.dry_run:
            log(f"dry-run: would send '{subject}'\n{body}")
        else:
            log(f"sending '{subject}'")
            sent = alerts.send_email(env, subject, body)
            # Without SMTP settings there is nothing to retry, so do not nag;
            # a configured server that failed is retried on the next run.
            if sent or not alerts.configured(env):
                for key, kind, _, _ in due:
                    acknowledge(tracked, key, kind, now)
    if not args.dry_run:
        directory.mkdir(parents=True, exist_ok=True)
        temp = state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=1))
        temp.replace(state_file)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
