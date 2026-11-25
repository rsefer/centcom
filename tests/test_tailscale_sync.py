import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ips = load_script("sync-tailscale-ips")
dns = load_script("sync-tailscale-dns")
CONFIG = """hosts = ["b-host", "a-host"]
[proxy]
primary = "b-host"
standby = "a-host"
[status]
port = 18086
container = "status-agent:8080"
[services.pihole]
container = "pihole:80"
placement = [{host = "b-host"}, {host = "a-host", port = 18085}]
"""


class TailscaleSyncTests(unittest.TestCase):
    def test_host_discovery_uses_registry_without_compose_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services.toml").write_text(CONFIG)
            with patch.object(ips, "ROOT", root):
                self.assertEqual(ips.hosts(), ["a-host", "b-host"])

    def test_dns_sync_uses_placement_order_and_shared_env_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "services.toml").write_text(CONFIG)
            (root / ".env").write_text("TAILSCALE_IP_B_HOST='100.1.1.2'\n"
                                      'TAILSCALE_IP_A_HOST="100.1.1.1"\n')
            with patch.object(dns, "ROOT", root):
                self.assertEqual(dns.pihole_addresses(),
                                 [("b-host", "100.1.1.2"), ("a-host", "100.1.1.1")])
                (root / ".env").write_text("TAILSCALE_IP_B_HOST=100.1.1.2\n")
                with self.assertRaisesRegex(SystemExit, "TAILSCALE_IP_A_HOST"):
                    dns.pihole_addresses()

    def test_ip_rewrite_handles_hyphenated_hosts_without_changing_other_keys(self):
        text, changes = ips.rewrite("OTHER=keep\nTAILSCALE_IP_B_HOST=100.1.1.3\n",
                                    {"b-host": "100.1.1.2", "a-host": "100.1.1.1"})
        self.assertEqual(text, "OTHER=keep\nTAILSCALE_IP_B_HOST=100.1.1.2\n"
                              "TAILSCALE_IP_A_HOST=100.1.1.1\n")
        self.assertEqual(len(changes), 2)


if __name__ == "__main__":
    unittest.main()
