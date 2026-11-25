import copy
import importlib.util
from pathlib import Path
import struct
import unittest
from unittest import mock
import urllib.error


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-services.py"
spec = importlib.util.spec_from_file_location("check_services", SCRIPT)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class ServiceChecksTests(unittest.TestCase):
    def test_roles_and_resolvers_follow_registry_not_fixed_host_names(self):
        config = copy.deepcopy(checks.registry.load())
        config["proxy"] = {"primary": "southcom", "standby": "northcom"}
        config["services"]["pihole"]["placement"] = [{"host": "eastcom"}]
        values = {"DOMAIN": "example.com", "TAILSCALE_IP_NORTHCOM": "100.64.0.1",
                  "TAILSCALE_IP_EASTCOM": "100.64.0.2",
                  "TAILSCALE_IP_SOUTHCOM": "100.64.0.3"}
        with mock.patch.object(checks.registry, "load", return_value=config), \
             mock.patch.object(checks, "settings", return_value=values), \
             mock.patch.object(checks, "dns_lookup", return_value=["100.64.0.3"]) as dns, \
             mock.patch.object(checks, "check_url", return_value=200):
            self.assertEqual(checks.main(), 0)
        dns.assert_called_once_with("100.64.0.2", "rss.example.com")

    def test_missing_resolver_addresses_fail_instead_of_skipping_dns_checks(self):
        config = copy.deepcopy(checks.registry.load())
        config["services"]["pihole"]["placement"] = [{"host": "eastcom"}]
        with mock.patch.object(checks.registry, "load", return_value=config), \
             mock.patch.object(checks, "settings", return_value={
                 "DOMAIN": "example.com", "TAILSCALE_IP_NORTHCOM": "100.64.0.1",
             }), mock.patch.object(checks, "dns_lookup") as dns:
            self.assertEqual(checks.main(), 1)
        dns.assert_not_called()

    def test_dns_query_checks_the_answer_address(self):
        class FakeSocket:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def settimeout(self, value):
                pass

            def connect(self, address):
                self.address = address

            def sendall(self, query):
                self.query = query

            def recv(self, size):
                return (self.query[:2] + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0)
                        + self.query[12:] + b"\xc0\x0c"
                        + struct.pack("!HHIH", 1, 1, 60, 4)
                        + bytes([100, 64, 0, 1]))

        with mock.patch.object(checks.socket, "socket", return_value=FakeSocket()):
            self.assertEqual(checks.dns_lookup("100.64.0.2", "rss.example.com"),
                             ["100.64.0.1"])

    def test_checks_fail_for_wrong_dns_or_unavailable_service(self):
        values = {"DOMAIN": "example.com", "TAILSCALE_IP_NORTHCOM": "100.64.0.1",
                  "TAILSCALE_IP_EASTCOM": "100.64.0.2"}
        with mock.patch.object(checks, "settings", return_value=values), \
             mock.patch.object(checks, "dns_lookup", return_value=["100.64.0.9"]), \
             mock.patch.object(checks, "check_url", side_effect=OSError("unavailable")):
            self.assertEqual(checks.main(), 1)

    def test_missing_configuration_fails(self):
        with mock.patch.object(checks, "settings", return_value={}):
            self.assertEqual(checks.main(), 1)

    def test_standby_is_accepted_when_both_resolvers_agree(self):
        values = {"DOMAIN": "example.com", "TAILSCALE_IP_NORTHCOM": "100.64.0.1",
                  "TAILSCALE_IP_EASTCOM": "100.64.0.2",
                  "TAILSCALE_IP_SOUTHCOM": "100.64.0.3"}
        with mock.patch.object(checks, "settings", return_value=values), \
             mock.patch.object(checks, "dns_lookup", return_value=["100.64.0.3"]), \
             mock.patch.object(checks, "check_url", return_value=200):
            self.assertEqual(checks.main(), 0)

    def test_resolver_disagreement_and_multiple_addresses_fail(self):
        values = {"DOMAIN": "example.com", "TAILSCALE_IP_NORTHCOM": "100.64.0.1",
                  "TAILSCALE_IP_EASTCOM": "100.64.0.2",
                  "TAILSCALE_IP_SOUTHCOM": "100.64.0.3"}
        for answers in ([["100.64.0.1"], ["100.64.0.3"]],
                        [["100.64.0.1", "100.64.0.3"], ["100.64.0.1"]]):
            with self.subTest(answers=answers), \
                 mock.patch.object(checks, "settings", return_value=values), \
                 mock.patch.object(checks, "dns_lookup", side_effect=answers), \
                 mock.patch.object(checks, "check_url", return_value=200):
                self.assertEqual(checks.main(), 1)

    def test_authentication_response_counts_as_reachable(self):
        error = urllib.error.HTTPError("https://example.com", 401, "Unauthorized", {}, None)
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(checks.urllib.request, "build_opener", return_value=opener):
            self.assertEqual(checks.check_url("https://example.com"), 401)


if __name__ == "__main__":
    unittest.main()
