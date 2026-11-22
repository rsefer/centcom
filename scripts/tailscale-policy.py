#!/usr/bin/env python3
"""Validate, diff, and apply tailscale/policy.hujson through the Tailscale API.

    tailscale-policy.py diff       show how the live policy differs (read-only)
    tailscale-policy.py validate   run the server-side validation and `tests` (read-only)
    tailscale-policy.py apply      validate, show the diff, then replace the live policy

Credentials (environment or .env, run from a trusted machine, not the Pis):

    TS_OAUTH_CLIENT_ID / TS_OAUTH_CLIENT_SECRET   OAuth client with the `policy_file` scope
    TS_API_KEY                                    alternative: a user API key (expires <= 90 days)

`apply` replaces the whole policy. It sends the ETag of the policy it diffed, so
the API rejects the write if someone edited the policy in the meantime.
"""

import difflib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tailscale" / "policy.hujson"


def settings(environ=os.environ, env_file=ROOT / ".env"):
    values = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and not key.strip().startswith("#"):
                values[key.strip()] = value.strip().strip("\"'")
    for key in ("TS_OAUTH_CLIENT_ID", "TS_OAUTH_CLIENT_SECRET", "TS_API_KEY", "TS_TAILNET", "TS_API_BASE"):
        if environ.get(key):
            values[key] = environ[key]
    return values


def request(base, path, token, method="GET", body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, response.headers, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read().decode()


def access_token(config):
    if config.get("TS_API_KEY"):
        return config["TS_API_KEY"]
    client_id, secret = config.get("TS_OAUTH_CLIENT_ID"), config.get("TS_OAUTH_CLIENT_SECRET")
    if not client_id or not secret:
        raise SystemExit("Set TS_OAUTH_CLIENT_ID and TS_OAUTH_CLIENT_SECRET (or TS_API_KEY) in .env")
    form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                   "client_secret": secret}).encode()
    req = urllib.request.Request(config.get("TS_API_BASE", "https://api.tailscale.com") + "/api/v2/oauth/token",
                                 data=form, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)["access_token"]
    except urllib.error.HTTPError as error:
        raise SystemExit(f"OAuth token request failed: HTTP {error.code} {error.read().decode()}")


def describe(status, text):
    try:
        data = json.loads(text)
    except ValueError:
        return f"HTTP {status} {text.strip()}"
    return f"HTTP {status} {json.dumps(data, indent=2)}"


def fetch_live(base, tailnet, token):
    status, headers, text = request(base, f"/api/v2/tailnet/{tailnet}/acl", token,
                                    headers={"Accept": "application/hujson"})
    if status != 200:
        raise SystemExit(f"Could not read the live policy: {describe(status, text)}")
    return text, headers.get("ETag")


def validate(base, tailnet, token, policy):
    status, _, text = request(base, f"/api/v2/tailnet/{tailnet}/acl/validate", token, "POST",
                              policy.encode(), {"Content-Type": "application/hujson"})
    if status == 200 and not text.strip().strip("{}").strip():
        return None
    return describe(status, text)


def diff(live, new):
    return "".join(difflib.unified_diff(live.splitlines(True), new.splitlines(True), "live", "tailscale/policy.hujson"))


def confirm(assume_yes):
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise SystemExit("Refusing to apply without a terminal; pass --yes to confirm.")
    return input("Replace the live policy with tailscale/policy.hujson? [y/N] ").strip().lower() == "y"


def main(argv):
    args = [arg for arg in argv if not arg.startswith("--")]
    flags = {arg for arg in argv if arg.startswith("--")}
    command = args[0] if args else ""
    if command not in ("diff", "validate", "apply") or flags - {"--yes"}:
        print(__doc__, file=sys.stderr)
        return 2
    config = settings()
    base = config.get("TS_API_BASE", "https://api.tailscale.com")
    tailnet = urllib.parse.quote(config.get("TS_TAILNET", "-"), safe="")
    token = access_token(config)
    new = POLICY.read_text()

    problem = validate(base, tailnet, token, new)
    if problem:
        print(f"Policy rejected:\n{problem}", file=sys.stderr)
        return 1
    if command == "validate":
        print("Policy is valid and all tests pass.")
        return 0

    live, etag = fetch_live(base, tailnet, token)
    changes = diff(live, new)
    if not changes:
        print("Live policy already matches tailscale/policy.hujson.")
        return 0
    print(changes)
    if command == "diff" or not confirm("--yes" in flags):
        return 0 if command == "diff" else 1

    headers = {"Content-Type": "application/hujson"}
    if etag:
        headers["If-Match"] = etag
    status, _, text = request(base, f"/api/v2/tailnet/{tailnet}/acl", token, "POST", new.encode(), headers)
    if status != 200:
        print(f"Apply failed: {describe(status, text)}", file=sys.stderr)
        return 1
    print("Policy applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
