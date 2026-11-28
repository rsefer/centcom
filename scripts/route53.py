#!/usr/bin/env python3
"""A minimal Amazon Route 53 client (standard library only).

Signs requests with AWS Signature Version 4 so the Pis need no AWS CLI or SDK.
It can list a hosted zone's record sets and UPSERT simple A records, which is all
proxy failover needs. The credentials are the ones Caddy already uses for its DNS
challenge (AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env); the IAM policy in
docs/networking.md already grants route53:ListResourceRecordSets and
route53:ChangeResourceRecordSets on the zone.
"""
import datetime
import hashlib
import hmac
import re
from urllib.error import HTTPError
from urllib.parse import quote
import urllib.request
from xml.etree import ElementTree
from xml.sax.saxutils import escape

HOST = "route53.amazonaws.com"
REGION = "us-east-1"   # Route 53 is global and always signs with us-east-1
NAMESPACE = "{https://route53.amazonaws.com/doc/2013-04-01/}"


def _hmac(key, message):
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign(method, host, path, query, body, access_key, secret_key, amz_date,
         region=REGION, service="route53"):
    """Return the headers (including Authorization) for a signed request."""
    date = amz_date[:8]
    canonical_query = "&".join(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    headers = f"host:{host}\nx-amz-date:{amz_date}\n"
    signed = "host;x-amz-date"
    canonical = "\n".join([method, path, canonical_query, headers, signed,
                           hashlib.sha256(body).hexdigest()])
    scope = f"{date}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), service),
                "aws4_request")
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {"x-amz-date": amz_date, "Authorization": (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, "
        f"Signature={signature}")}


def unescape(name):
    """Route 53 returns '*' as \\052; return a lowercase name without the final dot."""
    name = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), name)
    return name.rstrip(".").lower()


def parse_record_sets(xml_text):
    """Return (record dicts, next page parameters or None) from a list response."""
    root = ElementTree.fromstring(xml_text)

    def text(node, tag):
        found = node.find(NAMESPACE + tag)
        return found.text if found is not None else None

    records = []
    for node in root.iter(NAMESPACE + "ResourceRecordSet"):
        raw = text(node, "Name")
        ttl = text(node, "TTL")
        records.append({
            "raw_name": raw, "name": unescape(raw), "type": text(node, "Type"),
            "ttl": int(ttl) if ttl else None,
            "values": [r.text for r in node.iter(NAMESPACE + "Value")],
            "alias": node.find(NAMESPACE + "AliasTarget") is not None,
            "set_id": text(node, "SetIdentifier"),
        })
    following = None
    if text(root, "IsTruncated") == "true":
        following = {"name": text(root, "NextRecordName"), "type": text(root, "NextRecordType")}
        if text(root, "NextRecordIdentifier"):
            following["identifier"] = text(root, "NextRecordIdentifier")
    return records, following


def change_batch(comment, records, address):
    """XML request that UPSERTs each record (as returned by parse_record_sets) to address."""
    changes = "".join(
        "<Change><Action>UPSERT</Action><ResourceRecordSet>"
        f"<Name>{escape(r['raw_name'])}</Name><Type>A</Type><TTL>{r['ttl']}</TTL>"
        f"<ResourceRecords><ResourceRecord><Value>{escape(address)}</Value></ResourceRecord>"
        "</ResourceRecords></ResourceRecordSet></Change>" for r in records)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<ChangeResourceRecordSetsRequest xmlns="https://route53.amazonaws.com/doc/2013-04-01/">'
            f"<ChangeBatch><Comment>{escape(comment)}</Comment><Changes>{changes}</Changes>"
            "</ChangeBatch></ChangeResourceRecordSetsRequest>").encode()


def urllib_transport(method, url, headers, body):
    request = urllib.request.Request(url, data=body or None, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode(errors="replace")


class Route53:
    def __init__(self, zone_id, access_key, secret_key, transport=urllib_transport, clock=None):
        self.zone_id, self.access_key, self.secret_key = zone_id, access_key, secret_key
        self.transport = transport
        self.clock = clock or (lambda: datetime.datetime.now(datetime.timezone.utc))

    def request(self, method, query=None, body=b""):
        path = f"/2013-04-01/hostedzone/{quote(self.zone_id, safe='')}/rrset"
        query = query or {}
        headers = sign(method, HOST, path, query, body, self.access_key, self.secret_key,
                       self.clock().strftime("%Y%m%dT%H%M%SZ"))
        if body:
            headers["Content-Type"] = "text/xml"
        url = f"https://{HOST}{path}"
        if query:
            url += "?" + "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}"
                                  for k, v in sorted(query.items()))
        try:
            status, text = self.transport(method, url, headers, body)
        except OSError as error:
            raise RuntimeError(f"Route 53 {method} failed: {error}") from error
        if not 200 <= status < 300:
            code = re.search(r"<Message>(.*?)</Message>", text, re.S)
            raise RuntimeError(f"Route 53 {method} failed: HTTP {status} "
                               f"{(code.group(1) if code else text).strip()[:300]}")
        return text

    def list_records(self):
        """Every record set in the zone (follows pagination)."""
        records, query = [], {"maxitems": "300"}
        while True:
            page, following = parse_record_sets(self.request("GET", query))
            records += page
            if not following:
                return records
            query = {"maxitems": "300", "name": following["name"], "type": following["type"]}
            if "identifier" in following:
                query["identifier"] = following["identifier"]

    def upsert(self, comment, records, address):
        self.request("POST", body=change_batch(comment, records, address))
