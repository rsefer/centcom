#!/usr/bin/env python3

from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys


spec = importlib.util.spec_from_file_location("check_services", Path(__file__).with_name("check-services.py"))
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
BACKUP_MARKER = Path("/state/last-backup.json")


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


def dns_status(server, primary, domain):
    try:
        addresses = checks.dns_lookup(server, f"rss.{domain}")
        if primary not in addresses:
            return {"ok": False, "error": "Unexpected DNS answer"}
        return {"ok": True}
    except (OSError, ValueError, UnicodeError) as error:
        return {"ok": False, "error": str(error)}


def status():
    domain = os.environ["DOMAIN"]
    primary = os.environ["DNS_PRIMARY"]
    servers = {"primary": primary}
    if os.environ.get("DNS_SECONDARY"):
        servers["secondary"] = os.environ["DNS_SECONDARY"]
    return {
        "host": os.environ["STATUS_HOST"],
        "backup": backup_status(),
        "dns": {name: dns_status(server, primary, domain) for name, server in servers.items()},
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        data = json.dumps(status()).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    for key in ("DOMAIN", "DNS_PRIMARY", "STATUS_HOST"):
        if not os.environ.get(key):
            raise SystemExit(f"{key} must be set")
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
