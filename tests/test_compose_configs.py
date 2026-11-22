import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ComposeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            subprocess.run(["docker", "compose", "version"], check=True,
                           capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            raise unittest.SkipTest("Docker Compose is not available")

    def config(self, host):
        env = dict(os.environ, TAILSCALE_IP_NORTHCOM="100.73.192.117",
                   TAILSCALE_IP_SOUTHCOM="100.97.91.90",
                   TAILSCALE_IP_EASTCOM="100.127.122.57",
                   PIHOLE_NORTHCOM_PASSWORD="primary-test",
                   PIHOLE_EASTCOM_PASSWORD="replica-test", DOMAIN="example.com")
        result = subprocess.run(
            ["docker", "compose", "--env-file", ".env.sample",
             "-f", f"docker-compose.{host}.yml", "--profile", "opengym-mcp",
             "config", "--format", "json"],
            cwd=ROOT, env=env, capture_output=True, text=True, check=True,
        )
        config = json.loads(result.stdout)
        for service in config["services"].values():
            self.assertEqual(service["logging"], {
                "driver": "json-file",
                "options": {"max-size": "10m", "max-file": "3"},
            })
        return config

    def test_northcom_reuses_caddy_with_correct_paths_and_upstreams(self):
        config = self.config("northcom")
        self.assertEqual(set(config["services"]),
                         {"caddy", "pihole", "pihole-sync", "beszel-agent", "status-agent"})
        caddy = config["services"]["caddy"]
        self.assertEqual(caddy["build"]["context"], str(ROOT / "services/caddy"))
        self.assertEqual(caddy["environment"]["RSS_UPSTREAM"], "100.97.91.90:18084")
        mounts = {v["target"]: v["source"] for v in caddy["volumes"]}
        self.assertEqual(mounts["/etc/caddy/Caddyfile"],
                         str(ROOT / "services/caddy/Caddyfile"))
        self.assertTrue(all(p["host_ip"] == "100.73.192.117" for p in caddy["ports"]))
        agent = config["services"]["beszel-agent"]
        self.assertEqual(agent["network_mode"], "host")
        self.assertEqual(agent["environment"]["LISTEN"], "100.73.192.117:45876")
        self.assertEqual(agent["environment"]["HUB_URL"], "")
        self.assertEqual(agent["environment"]["TOKEN"], "")

    def test_caddy_upstreams_resolve_per_host(self):
        southcom, eastcom = "100.97.91.90", "100.127.122.57"
        northcom = "100.73.192.117"
        expected = {
            "northcom": {
                "PIHOLE_UPSTREAM": "pihole:80", "RSS_UPSTREAM": f"{southcom}:18084",
                "DRAW_UPSTREAM": f"{southcom}:18081", "GYM_UPSTREAM": f"{southcom}:18082",
                "BESZEL_UPSTREAM": f"{southcom}:18090", "GYM_MCP_UPSTREAM": f"{southcom}:18083",
                "STATUS_NORTHCOM_UPSTREAM": "status-agent:8080",
                "STATUS_SOUTHCOM_UPSTREAM": f"{southcom}:18086",
                "STATUS_EASTCOM_UPSTREAM": f"{eastcom}:18086",
            },
            "southcom": {
                "PIHOLE_UPSTREAM": f"{eastcom}:18085", "RSS_UPSTREAM": "rss:80",
                "DRAW_UPSTREAM": "excalidraw:80", "GYM_UPSTREAM": "opengym-app:80",
                "BESZEL_UPSTREAM": "beszel:8090", "GYM_MCP_UPSTREAM": "opengym-mcp:8000",
                "STATUS_NORTHCOM_UPSTREAM": f"{northcom}:18086",
                "STATUS_SOUTHCOM_UPSTREAM": "status-agent:8080",
                "STATUS_EASTCOM_UPSTREAM": f"{eastcom}:18086",
            },
        }
        for host, upstreams in expected.items():
            environment = self.config(host)["services"]["caddy"]["environment"]
            self.assertEqual({k: v for k, v in environment.items() if k.endswith("_UPSTREAM")},
                             upstreams, host)

    def test_status_agents_mount_the_service_registry(self):
        for host in ("northcom", "southcom", "eastcom"):
            targets = {v["target"] for v in
                       self.config(host)["services"]["status-agent"]["volumes"]}
            self.assertTrue({"/app/services.py", "/app/services.toml"} <= targets, host)

    def test_eastcom_uses_only_its_own_tailscale_ip(self):
        config = self.config("eastcom")
        self.assertEqual(set(config["services"]), {"pihole", "beszel-agent", "status-agent"})
        pihole = config["services"]["pihole"]
        self.assertEqual(pihole["environment"]["FTLCONF_dns_listeningMode"], "ALL")
        self.assertEqual(
            {(p["host_ip"], p["published"], p["target"], p["protocol"])
             for p in pihole["ports"]},
            {("100.127.122.57", "53", 53, "tcp"),
             ("100.127.122.57", "53", 53, "udp"),
             ("100.127.122.57", "18085", 80, "tcp")},
        )
        self.assertEqual(config["services"]["beszel-agent"]["environment"]["LISTEN"],
                         "100.127.122.57:45876")
        self.assertEqual(config["services"]["status-agent"]["environment"]["STATUS_HOST"], "eastcom")
        self.assertEqual(
            {p["host_ip"] for p in config["services"]["status-agent"]["ports"]},
            {"100.127.122.57"},
        )

    def test_southcom_reuses_service_paths_and_binds_to_tailnet(self):
        config = self.config("southcom")
        services = config["services"]
        caddy = services["caddy"]
        self.assertEqual({p["host_ip"] for p in caddy["ports"]}, {"100.97.91.90"})
        self.assertEqual(caddy["environment"]["PIHOLE_UPSTREAM"], "100.127.122.57:18085")
        self.assertEqual(caddy["environment"]["STATUS_NORTHCOM_UPSTREAM"], "100.73.192.117:18086")
        self.assertEqual(caddy["environment"]["STATUS_SOUTHCOM_UPSTREAM"], "status-agent:8080")
        self.assertEqual(caddy["environment"]["STATUS_EASTCOM_UPSTREAM"], "100.127.122.57:18086")
        self.assertEqual(caddy["environment"]["RSS_UPSTREAM"], "rss:80")
        self.assertEqual(caddy["build"]["context"], str(ROOT / "services/caddy"))
        self.assertEqual({v["target"]: v["source"] for v in caddy["volumes"]}["/etc/caddy/Caddyfile"],
                         str(ROOT / "services/caddy/Caddyfile"))
        rss = services["rss"]
        mounts = {v["target"]: v["source"] for v in rss["volumes"]}
        self.assertEqual(mounts["/var/www/FreshRSS/data"], str(ROOT / "data"))
        self.assertEqual(mounts["/var/www/FreshRSS/extensions"], str(ROOT / "extensions"))
        self.assertEqual(services["opengym-mcp"]["build"]["context"],
                         str(ROOT / "services/opengym/mcp"))
        status = services["status-agent"]
        self.assertEqual(status["environment"]["STATUS_HOST"], "southcom")
        self.assertEqual({p["host_ip"] for p in status["ports"]}, {"100.97.91.90"})
        self.assertEqual({v["target"]: v["source"] for v in status["volumes"]}["/app/status-agent.py"],
                         str(ROOT / "scripts/status-agent.py"))
        for service in ("beszel", "excalidraw", "opengym-app", "opengym-mcp", "rss"):
            self.assertIn("100.97.91.90", {p["host_ip"] for p in services[service]["ports"]})


if __name__ == "__main__":
    unittest.main()
