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

    def test_status_checks_both_resolvers_and_reports_failure(self):
        env = {"STATUS_HOST": "northcom", "DOMAIN": "example.com",
               "DNS_PRIMARY": "100.64.0.1", "DNS_SECONDARY": "100.64.0.2"}
        with mock.patch.dict(os.environ, env), \
             mock.patch.object(agent, "backup_status", return_value={"last_success": None}), \
             mock.patch.object(agent.checks, "dns_lookup",
                               side_effect=[["100.64.0.1"], OSError("timeout")]):
            status = agent.status()
        self.assertEqual(status["host"], "northcom")
        self.assertTrue(status["dns"]["primary"]["ok"])
        self.assertFalse(status["dns"]["secondary"]["ok"])
        self.assertIn("timeout", status["dns"]["secondary"]["error"])

    def test_unexpected_dns_answer_is_a_failure(self):
        with mock.patch.object(agent.checks, "dns_lookup", return_value=["100.64.0.9"]):
            self.assertFalse(agent.dns_status("100.64.0.2", "100.64.0.1",
                                              "example.com")["ok"])

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


if __name__ == "__main__":
    unittest.main()
