import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-proxy.sh"


class ProxyCheckTests(unittest.TestCase):
    def test_checks_every_original_hostname_and_reports_upstream_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$TEST_LOG"\n'
                'case "$*" in *rss.example.com*) printf 502;; *) printf 200;; esac\n'
            )
            curl.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:{os.environ['PATH']}",
                       TEST_LOG=str(root / "curl.log"), CHECK_MCP="1")
            result = subprocess.run(
                ["sh", str(SCRIPT), "example.com", "100.64.0.3"],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("rss.example.com/: unexpected HTTP 502", result.stderr)
            calls = (root / "curl.log").read_text().splitlines()
            self.assertEqual(len(calls), 11)
            self.assertTrue(any("gym.example.com:443:100.64.0.3" in call for call in calls))
            self.assertTrue(any("https://pihole.example.com/admin/" in call for call in calls))
            self.assertTrue(any("https://gym-mcp.example.com/mcp" in call for call in calls))

    def test_invalid_proxy_address_fails_before_network_access(self):
        result = subprocess.run(["sh", str(SCRIPT), "example.com", "not-an-ip"],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid IPv4", result.stderr)


if __name__ == "__main__":
    unittest.main()
