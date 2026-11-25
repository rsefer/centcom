#!/usr/bin/env python3

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import urllib.error


spec = importlib.util.spec_from_file_location("check_services", Path(__file__).with_name("check-services.py"))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
BACKUP_MARKER = Path("/state/last-backup.json")
SELFHEAL_DIR = Path("/selfheal")
UPTIME_FILE = Path("/proc/uptime")


def backup_status():
    try:
        with BACKUP_MARKER.open() as marker:
            data = json.load(marker)
        if (not isinstance(data, dict) or not isinstance(data.get("offsite"), bool)
                or not isinstance(data.get("last_success"), str)):
            raise ValueError("invalid backup marker")
        datetime.fromisoformat(data["last_success"].replace("Z", "+00:00"))
        return {"last_success": data["last_success"], "offsite": data["offsite"]}
    except FileNotFoundError:
        return {"last_success": None, "offsite": None}
    except (OSError, ValueError, TypeError, KeyError) as error:
        return {"last_success": None, "offsite": None, "error": str(error)}


def selfheal_status():
    result = {"uptime_seconds": None, "events": []}
    try:
        result["uptime_seconds"] = int(float(UPTIME_FILE.read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        pass
    try:
        lines = (SELFHEAL_DIR / "events.log").read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return result
    except OSError as error:
        result["error"] = str(error)
        return result
    result["events"] = [line[:200] for line in lines[-5:]]
    return result


def dns_status(server, primary, domain, standby=None):
    try:
        addresses = checks.dns_lookup(server, f"rss.{domain}")
        if len(addresses) != 1 or addresses[0] not in (primary, standby):
            return {"ok": False, "error": "Unexpected DNS answer"}
        return {"ok": True, "proxy": "primary" if primary in addresses else "standby"}
    except (OSError, ValueError, UnicodeError) as error:
        return {"ok": False, "error": str(error)}


def status():
    domain = os.environ["DOMAIN"]
    primary = os.environ["DNS_PRIMARY_PROXY"]
    servers = json.loads(os.environ["DNS_RESOLVERS"])
    return {
        "host": os.environ["STATUS_HOST"],
        "backup": backup_status(),
        "selfheal": selfheal_status(),
        "dns": {
            name: dns_status(server, primary, domain, os.environ.get("DNS_STANDBY_PROXY"))
            for name, server in servers.items() if server
        },
    }


def application_status(service, path, domain):
    url = f"https://{service}.{domain}{path}"
    try:
        return {"ok": True, "status": checks.check_url(url)}
    except urllib.error.HTTPError as error:
        return {"ok": False, "error": f"HTTP {error.code}"}
    except (OSError, urllib.error.URLError, ValueError) as error:
        return {"ok": False, "error": str(error)}


def applications_status():
    domain = os.environ["DOMAIN"]
    applications = checks.registry.applications(checks.registry.load())
    with ThreadPoolExecutor(max_workers=len(applications)) as executor:
        futures = {
            service: executor.submit(application_status, service, path, domain)
            for service, path in applications.items()
        }
        return {"applications": {service: future.result() for service, future in futures.items()}}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            result = status()
        elif self.path == "/status/applications":
            result = applications_status()
        else:
            self.send_error(404)
            return
        data = json.dumps(result).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    for key in ("DOMAIN", "DNS_PRIMARY_PROXY", "DNS_RESOLVERS", "STATUS_HOST"):
        if not os.environ.get(key):
            raise SystemExit(f"{key} must be set")
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
