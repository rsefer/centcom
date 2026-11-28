import importlib.util
from pathlib import Path
import unittest
from xml.etree import ElementTree

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "route53.py"
spec = importlib.util.spec_from_file_location("route53", SCRIPT)
r53 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r53)

NS = 'xmlns="https://route53.amazonaws.com/doc/2013-04-01/"'


def listing(records, truncated=None):
    body = "".join(records)
    extra = ""
    if truncated:
        extra = (f"<IsTruncated>true</IsTruncated><NextRecordName>{truncated[0]}</NextRecordName>"
                 f"<NextRecordType>{truncated[1]}</NextRecordType>")
    else:
        extra = "<IsTruncated>false</IsTruncated>"
    return (f'<ListResourceRecordSetsResponse {NS}><ResourceRecordSets>{body}'
            f"</ResourceRecordSets>{extra}</ListResourceRecordSetsResponse>")


def a_record(name, address, ttl=300):
    return (f"<ResourceRecordSet><Name>{name}</Name><Type>A</Type><TTL>{ttl}</TTL>"
            f"<ResourceRecords><ResourceRecord><Value>{address}</Value></ResourceRecord>"
            "</ResourceRecords></ResourceRecordSet>")


class Signing(unittest.TestCase):
    def test_matches_the_aws_get_vanilla_test_vector(self):
        headers = r53.sign("GET", "example.amazonaws.com", "/", {}, b"", "AKIDEXAMPLE",
                           "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", "20150830T123600Z",
                           region="us-east-1", service="service")
        self.assertEqual(headers["Authorization"], (
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
            "SignedHeaders=host;x-amz-date, Signature="
            "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"))

    def test_query_parameters_are_sorted_and_encoded_in_the_signature(self):
        a = r53.sign("GET", "h", "/p", {"b": "2", "a": "x y"}, b"", "K", "S", "20200101T000000Z")
        b = r53.sign("GET", "h", "/p", {"a": "x y", "b": "2"}, b"", "K", "S", "20200101T000000Z")
        self.assertEqual(a, b)
        self.assertNotEqual(a, r53.sign("GET", "h", "/p", {"a": "x+y", "b": "2"}, b"", "K", "S",
                                        "20200101T000000Z"))


class Parsing(unittest.TestCase):
    def test_names_are_unescaped_and_lowercased(self):
        self.assertEqual(r53.unescape("\\052.VPN.Example.com."), "*.vpn.example.com")

    def test_parses_values_ttl_aliases_and_pagination(self):
        xml = listing([
            a_record("\\052.vpn.example.com.", "100.1.1.1"),
            "<ResourceRecordSet><Name>alias.example.com.</Name><Type>A</Type><AliasTarget>"
            "<HostedZoneId>Z</HostedZoneId><DNSName>x.</DNSName><EvaluateTargetHealth>false"
            "</EvaluateTargetHealth></AliasTarget></ResourceRecordSet>",
            "<ResourceRecordSet><Name>w.example.com.</Name><Type>A</Type>"
            "<SetIdentifier>one</SetIdentifier><Weight>1</Weight><TTL>60</TTL><ResourceRecords>"
            "<ResourceRecord><Value>1.2.3.4</Value></ResourceRecord></ResourceRecords></ResourceRecordSet>",
        ], truncated=("next.example.com.", "A"))
        records, following = r53.parse_record_sets(xml)
        wildcard, alias, weighted = records
        self.assertEqual((wildcard["name"], wildcard["raw_name"], wildcard["values"], wildcard["ttl"]),
                         ("*.vpn.example.com", "\\052.vpn.example.com.", ["100.1.1.1"], 300))
        self.assertTrue(alias["alias"])
        self.assertEqual(weighted["set_id"], "one")
        self.assertEqual(following, {"name": "next.example.com.", "type": "A"})

    def test_change_batch_upserts_with_the_original_name_and_ttl(self):
        records, _ = r53.parse_record_sets(listing([a_record("\\052.vpn.example.com.", "100.1.1.1", 60)]))
        root = ElementTree.fromstring(r53.change_batch("why <now>", records, "100.1.1.2"))
        ns = {"r": "https://route53.amazonaws.com/doc/2013-04-01/"}
        self.assertEqual(root.find(".//r:Action", ns).text, "UPSERT")
        self.assertEqual(root.find(".//r:Name", ns).text, "\\052.vpn.example.com.")
        self.assertEqual(root.find(".//r:TTL", ns).text, "60")
        self.assertEqual(root.find(".//r:Value", ns).text, "100.1.1.2")
        self.assertEqual(root.find(".//r:Comment", ns).text, "why <now>")


class Client(unittest.TestCase):
    def client(self, responses):
        self.calls = []

        def transport(method, url, headers, body):
            self.calls.append((method, url, headers, body))
            return responses.pop(0)
        return r53.Route53("ZONE1", "AKIA", "secret", transport=transport,
                           clock=lambda: __import__("datetime").datetime(2026, 1, 2, 3, 4, 5))

    def test_list_follows_pagination_and_signs_every_request(self):
        client = self.client([
            (200, listing([a_record("a.example.com.", "1.1.1.1")], truncated=("b.example.com.", "A"))),
            (200, listing([a_record("b.example.com.", "2.2.2.2")])),
        ])
        records = client.list_records()
        self.assertEqual([r["name"] for r in records], ["a.example.com", "b.example.com"])
        self.assertEqual(len(self.calls), 2)
        self.assertIn("name=b.example.com.", self.calls[1][1])
        for _, url, headers, _ in self.calls:
            self.assertTrue(url.startswith("https://route53.amazonaws.com/2013-04-01/hostedzone/ZONE1/rrset"))
            self.assertIn("Credential=AKIA/20260102/us-east-1/route53/aws4_request",
                          headers["Authorization"])

    def test_upsert_posts_xml_and_errors_carry_the_aws_message(self):
        client = self.client([(200, "<ok/>")])
        records, _ = r53.parse_record_sets(listing([a_record("a.example.com.", "1.1.1.1")]))
        client.upsert("test", records, "9.9.9.9")
        method, _, headers, body = self.calls[0]
        self.assertEqual((method, headers["Content-Type"]), ("POST", "text/xml"))
        self.assertIn(b"9.9.9.9", body)
        failing = self.client([(403, "<Error><Message>Access denied for you</Message></Error>")])
        with self.assertRaisesRegex(RuntimeError, "HTTP 403 Access denied for you"):
            failing.list_records()

    def test_network_errors_become_runtime_errors(self):
        def transport(*args):
            raise OSError("unreachable")
        with self.assertRaisesRegex(RuntimeError, "unreachable"):
            r53.Route53("Z", "K", "S", transport=transport).list_records()


if __name__ == "__main__":
    unittest.main()
