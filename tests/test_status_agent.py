import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "status-agent.py"
spec = importlib.util.spec_from_file_location("status_agent", SCRIPT)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


class StatusAgentTests(unittest.TestCase):
    def test_backup_marker_reports_only_the_latest_success(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "last-backup.json"
            with mock.patch.object(agent, "BACKUP_MARKER", marker):
                self.assertIsNone(agent.backup_status()["last_success"])
                marker.write_text(json.dumps({
                    "last_success": "2026-11-21T12:00:00Z", "offsite": True,
                }))
                self.assertEqual(agent.backup_status(), {
                    "last_success": "2026-11-21T12:00:00Z", "offsite": True,
                })
                marker.write_text("invalid")
                self.assertIn("error", agent.backup_status())
                marker.write_text('{"last_success": 42, "offsite": true}')
                self.assertIn("error", agent.backup_status())

    def test_selfheal_status_reports_uptime_and_recent_events(self):
        with tempfile.TemporaryDirectory() as temp:
            uptime = Path(temp) / "uptime"
            uptime.write_text("93784.12 1000.00\n")
            with mock.patch.object(agent, "SELFHEAL_DIR", Path(temp)), \
                 mock.patch.object(agent, "UPTIME_FILE", uptime):
                self.assertEqual(agent.selfheal_status(), {"uptime_seconds": 93784, "events": []})
                lines = [f"2026-11-24 07:0{n}:00 event {n}" for n in range(7)]
                (Path(temp) / "events.log").write_text("\n".join(lines) + "\n")
                self.assertEqual(agent.selfheal_status()["events"], lines[-5:])
            with mock.patch.object(agent, "SELFHEAL_DIR", Path(temp) / "missing"), \
                 mock.patch.object(agent, "UPTIME_FILE", Path(temp) / "none"):
                self.assertEqual(agent.selfheal_status(), {"uptime_seconds": None, "events": []})

    def test_status_checks_both_resolvers_and_reports_failure(self):
        env = {"STATUS_HOST": "northcom", "DOMAIN": "example.com",
               "DNS_PRIMARY_PROXY": "100.64.0.1",
               "DNS_RESOLVERS": json.dumps({"primary": "100.64.0.1", "secondary": "100.64.0.2"})}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(agent, "backup_status", return_value={"last_success": None}), \
             mock.patch.object(agent, "selfheal_status", return_value={"events": []}), \
             mock.patch.object(agent.checks, "dns_lookup",
                               side_effect=[["100.64.0.1"], OSError("timeout")]):
            status = agent.status()
        self.assertEqual(status["host"], "northcom")
        self.assertTrue(status["dns"]["primary"]["ok"])
        self.assertFalse(status["dns"]["secondary"]["ok"])
        self.assertIn("timeout", status["dns"]["secondary"]["error"])

    def test_resolver_and_proxy_hosts_can_differ_and_optional_resolver_is_omitted(self):
        env = {"STATUS_HOST": "southcom", "DOMAIN": "example.com",
               "DNS_PRIMARY_PROXY": "100.64.0.3",
               "DNS_RESOLVERS": json.dumps({"primary": "100.64.0.2", "secondary": ""})}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(agent, "backup_status", return_value={}), \
             mock.patch.object(agent, "selfheal_status", return_value={}), \
             mock.patch.object(agent.checks, "dns_lookup",
                               return_value=["100.64.0.3"]) as dns:
            result = agent.status()
        self.assertEqual(result["dns"], {"primary": {"ok": True, "proxy": "primary"}})
        dns.assert_called_once_with("100.64.0.2", "rss.example.com")

    def test_unexpected_dns_answer_is_a_failure(self):
        with mock.patch.object(agent.checks, "dns_lookup", return_value=["100.64.0.9"]):
            self.assertFalse(agent.dns_status("100.64.0.2", "100.64.0.1",
                                              "example.com")["ok"])

    def test_dns_accepts_the_standby_proxy(self):
        with mock.patch.object(agent.checks, "dns_lookup", return_value=["100.64.0.3"]):
            self.assertEqual(agent.dns_status("100.64.0.2", "100.64.0.1",
                                              "example.com", "100.64.0.3"),
                             {"ok": True, "proxy": "standby"})

    def test_application_checks_distinguish_http_responses_and_failures(self):
        def check(url):
            if url == "https://rss.example.com/":
                raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)
            if url == "https://gym.example.com/":
                raise urllib.error.URLError("connection failed")
            return 302

        with mock.patch.dict(os.environ, {"DOMAIN": "example.com"}), \
             mock.patch.object(agent.checks, "check_url", side_effect=check):
            applications = agent.applications_status()["applications"]
        self.assertEqual(applications["ha"], {"ok": True, "status": 302})
        self.assertEqual(applications["rss"], {"ok": False, "error": "HTTP 503"})
        self.assertFalse(applications["gym"]["ok"])
        self.assertIn("connection failed", applications["gym"]["error"])

    def test_http_endpoint_returns_current_status(self):
        server = agent.ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
        worker = threading.Thread(target=server.serve_forever)
        worker.start()
        self.addCleanup(worker.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}"
        with mock.patch.object(agent, "status", return_value={"host": "northcom"}):
            with urllib.request.urlopen(url + "/") as response:
                self.assertEqual(json.load(response), {"host": "northcom"})
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(url + "/other")
            self.assertEqual(error.exception.code, 404)
        with mock.patch.object(agent, "applications_status",
                               return_value={"applications": {"rss": {"ok": True, "status": 200}}}):
            with urllib.request.urlopen(url + "/status/applications") as response:
                self.assertEqual(json.load(response)["applications"]["rss"]["status"], 200)
        with mock.patch.object(agent, "status", side_effect=AssertionError("must not probe DNS")) as status, \
             mock.patch.object(agent, "applications_status",
                               side_effect=AssertionError("must not probe applications")) as applications:
            with urllib.request.urlopen(url + "/healthz") as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(json.load(response), {"ok": True})
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            status.assert_not_called()
            applications.assert_not_called()


if __name__ == "__main__":
    unittest.main()
