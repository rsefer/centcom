import importlib.util
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tailscale-policy.py"
spec = importlib.util.spec_from_file_location("tailscale_policy", SCRIPT)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class FakeTailscale(BaseHTTPRequestHandler):
    live = '{"grants": []}\n'
    valid = True
    seen = []

    def log_message(self, *args):
        pass

    def reply(self, status, body="", headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body.encode())

    def do_GET(self):
        FakeTailscale.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self.reply(200, self.live, {"ETag": '"abc"'})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        FakeTailscale.seen.append(("POST", self.path, self.headers.get("If-Match"), body))
        if self.path.endswith("/validate"):
            self.reply(200 if self.valid else 400, "" if self.valid else json.dumps({"message": "test failed"}))
        elif self.path.endswith("/oauth/token"):
            self.reply(200, json.dumps({"access_token": "tok"}))
        else:
            self.reply(200, "{}")


class TailscalePolicyTests(unittest.TestCase):
    def setUp(self):
        FakeTailscale.seen, FakeTailscale.valid = [], True
        self.server = HTTPServer(("127.0.0.1", 0), FakeTailscale)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_settings_prefers_environment_over_env_file(self):
        with tempfile.TemporaryDirectory() as temp:
            env = Path(temp) / ".env"
            env.write_text("# TS_API_KEY=commented\nTS_API_KEY=file\nTS_TAILNET='example.com'\n")
            values = policy.settings({"TS_API_KEY": "env"}, env)
        self.assertEqual(values, {"TS_API_KEY": "env", "TS_TAILNET": "example.com"})

    def test_oauth_client_is_exchanged_for_a_token(self):
        token = policy.access_token({"TS_OAUTH_CLIENT_ID": "id", "TS_OAUTH_CLIENT_SECRET": "s", "TS_API_BASE": self.base})
        self.assertEqual(token, "tok")

    def test_validate_reports_server_rejection(self):
        self.assertIsNone(policy.validate(self.base, "-", "tok", "{}"))
        FakeTailscale.valid = False
        self.assertIn("test failed", policy.validate(self.base, "-", "tok", "{}"))

    def test_diff_is_empty_for_identical_policies(self):
        self.assertEqual(policy.diff("a\n", "a\n"), "")
        self.assertIn("+b", policy.diff("a\n", "a\nb\n"))

    def test_apply_sends_the_etag_it_diffed(self):
        live, etag = policy.fetch_live(self.base, "-", "tok")
        self.assertEqual((live, etag), (FakeTailscale.live, '"abc"'))
        status, *_ = policy.request(self.base, "/api/v2/tailnet/-/acl", "tok", "POST", b"{}", {"If-Match": etag})
        self.assertEqual(status, 200)
        self.assertEqual(FakeTailscale.seen[-1][2], '"abc"')


if __name__ == "__main__":
    unittest.main()
