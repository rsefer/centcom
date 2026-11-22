#!/usr/bin/env python3

import importlib.util
import os
from pathlib import Path
import secrets
import socket
import struct
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("centcom_services", Path(__file__).with_name("services.py"))
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


def settings():
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key.strip()] = value.strip().strip("\"'")
    for key in ("DOMAIN", "TAILSCALE_IP", "TAILSCALE_IP_NORTHCOM", "TAILSCALE_IP_SOUTHCOM", "TAILSCALE_IP_EASTCOM"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def dns_lookup(server, hostname):
    labels = hostname.rstrip(".").split(".")
    question = b"".join(bytes([len(label)]) + label.encode("ascii") for label in labels)
    question += b"\0" + struct.pack("!HH", 1, 1)
    query_id = secrets.randbits(16)
    query = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + question
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(5)
        sock.connect((server, 53))
        sock.sendall(query)
        response = sock.recv(4096)
    if len(response) < 12:
        raise ValueError("short DNS response")
    response_id, flags, questions, answers, _, _ = struct.unpack("!HHHHHH", response[:12])
    if response_id != query_id or not flags & 0x8000 or flags & 0x020F or questions != 1:
        raise ValueError("invalid or unsuccessful DNS response")

    def skip_name(offset):
        while True:
            length = response[offset]
            offset += 1
            if length == 0:
                return offset
            if length & 0xC0 == 0xC0:
                return offset + 1
            if length & 0xC0:
                raise ValueError("invalid DNS name")
            offset += length

    try:
        offset = skip_name(12) + 4
        addresses = []
        for _ in range(answers):
            offset = skip_name(offset)
            kind, cls, _, length = struct.unpack_from("!HHIH", response, offset)
            offset += 10
            data = response[offset:offset + length]
            if len(data) != length:
                raise ValueError("truncated DNS answer")
            if kind == 1 and cls == 1 and length == 4:
                addresses.append(socket.inet_ntoa(data))
            offset += length
        return addresses
    except (IndexError, struct.error) as error:
        raise ValueError("malformed DNS response") from error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_url(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(url, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as error:
        if error.code in (301, 302, 303, 307, 308, 401, 403):
            return error.code
        raise


def main():
    values = settings()
    domain = values.get("DOMAIN", "")
    primary = values.get("TAILSCALE_IP_NORTHCOM") or values.get("TAILSCALE_IP")
    standby = values.get("TAILSCALE_IP_SOUTHCOM")
    secondary = values.get("TAILSCALE_IP_EASTCOM")
    if not domain or not primary:
        print("Set DOMAIN and TAILSCALE_IP_NORTHCOM (or TAILSCALE_IP) in .env", file=sys.stderr)
        return 1
    failed = False
    resolved_proxies = []
    for server in filter(None, (primary, secondary)):
        try:
            addresses = dns_lookup(server, f"rss.{domain}")
            allowed = {address for address in (primary, standby) if address}
            found = set(addresses) & allowed
            if len(found) != 1 or set(addresses) != found:
                raise ValueError(f"expected one configured proxy address, got {addresses}")
            proxy = found.pop()
            resolved_proxies.append(proxy)
            print(f"DNS {server}: rss.{domain} resolves to {proxy}")
        except (OSError, ValueError, UnicodeError) as error:
            print(f"DNS {server}: {error}", file=sys.stderr)
            failed = True
    if len(set(resolved_proxies)) > 1:
        print("DNS resolvers disagree on the active proxy", file=sys.stderr)
        failed = True
    for name, path, _ in registry.checks(registry.load(), "deploy"):
        url = f"https://{domain if name == 'dashboard' else f'{name}.{domain}'}{path}"
        try:
            status = check_url(url)
            print(f"{url}: HTTP {status}")
        except (OSError, urllib.error.URLError, ValueError) as error:
            print(f"{url}: {error}", file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
