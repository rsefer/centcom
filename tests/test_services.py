import copy
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "services.py"
spec = importlib.util.spec_from_file_location("centcom_services", SCRIPT)
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)

SAMPLE = {
    "hosts": ["a", "b"],
    "proxy": {"primary": "a", "standby": "b"},
    "status": {"port": 9000, "container": "status:80"},
    "services": {
        "app": {"title": "App", "icon": "app.svg", "dashboard": "primary", "path": "/x/",
                "container": "app:80", "placement": [{"host": "b", "port": 8001}],
                "checks": ["deploy", "failover"]},
        "dns": {"container": "dns:80", "placement": [{"host": "a"}, {"host": "b", "port": 8002}]},
        "ext": {"tailnet": "machine", "checks": ["failover"], "optional": True},
    },
}


class ServicesTests(unittest.TestCase):
    def test_upstreams_use_container_names_on_the_same_host(self):
        values = registry.upstreams(SAMPLE, "b")
        self.assertEqual(values["APP_UPSTREAM"], "app:80")
        self.assertEqual(values["STATUS_B_UPSTREAM"], "status:80")

    def test_upstreams_use_published_ports_on_other_hosts(self):
        values = registry.upstreams(SAMPLE, "a")
        self.assertEqual(values["APP_UPSTREAM"], "${TAILSCALE_IP_B:?Set TAILSCALE_IP_B}:8001")
        self.assertEqual(values["STATUS_B_UPSTREAM"], "${TAILSCALE_IP_B:?Set TAILSCALE_IP_B}:9000")
        self.assertEqual(values["STATUS_A_UPSTREAM"], "status:80")

    def test_placement_prefers_local_then_first_published_port(self):
        self.assertEqual(registry.upstreams(SAMPLE, "a")["DNS_UPSTREAM"], "dns:80")
        config = copy.deepcopy(SAMPLE)
        config["hosts"].append("c")
        self.assertEqual(registry.upstreams(config, "c")["DNS_UPSTREAM"],
                         "${TAILSCALE_IP_B:?Set TAILSCALE_IP_B}:8002")

    def test_single_host_uses_containers_everywhere(self):
        values = registry.upstreams(SAMPLE)
        self.assertEqual(set(values.values()), {"app:80", "dns:80", "status:80"})

    def test_unreachable_service_is_an_error(self):
        config = copy.deepcopy(SAMPLE)
        config["services"]["app"]["placement"] = [{"host": "b"}]
        with self.assertRaises(ValueError):
            registry.upstreams(config, "a")

    def test_probe_lists(self):
        self.assertEqual(registry.checks(SAMPLE, "deploy"),
                         [("dashboard", "/", False), ("app", "/x/", False)])
        self.assertEqual(registry.checks(SAMPLE, "failover")[-1], ("ext", "/", True))
        self.assertEqual(registry.applications(SAMPLE), {"app": "/x/"})

    def test_validation_rejects_bad_configuration(self):
        for mutate in (
            lambda c: c["proxy"].update(primary="zzz"),
            lambda c: c["services"]["app"].update(tailnet="x"),
            lambda c: c["services"]["app"].update(color="red"),
            lambda c: c["services"]["app"]["placement"].append({"host": "nope"}),
            lambda c: c["services"]["app"].update(dashboard="side"),
            lambda c: c["services"]["app"].update(checks=["other"]),
            lambda c: c["hosts"].append("a"),
        ):
            config = copy.deepcopy(SAMPLE)
            mutate(config)
            with self.assertRaises(ValueError):
                registry.validate(config)
        registry.validate(SAMPLE)

    def test_change_batch_upserts_apex_and_wildcard(self):
        host, records = registry.dns_records(
            SAMPLE, "standby", {"DOMAIN": "vpn.example.com", "TAILSCALE_IP_B": "100.1.1.2"})
        batch = registry.change_batch(host, records)
        names = [c["ResourceRecordSet"]["Name"] for c in batch["Changes"]]
        self.assertEqual(names, ["vpn.example.com.", "*.vpn.example.com."])
        self.assertEqual({c["Action"] for c in batch["Changes"]}, {"UPSERT"})


class RepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = registry.load(ROOT / "services.toml")

    def test_generated_files_are_current(self):
        for relative, text in registry.outputs(self.config).items():
            self.assertEqual((ROOT / relative).read_text(), text,
                             f"{relative} is stale; run `make generate`")

    def test_hosts_match_host_compose_files(self):
        found = sorted(re.match(r"docker-compose\.(.+)\.yml", p.name).group(1)
                       for p in ROOT.glob("docker-compose.*.yml"))
        self.assertEqual(sorted(self.config["hosts"]), found)

    def test_caddyfile_matches_services(self):
        caddyfile = (ROOT / "services/caddy/Caddyfile").read_text()
        sites = set(re.findall(r"^([a-z0-9-]+)\.\{\$DOMAIN\} \{", caddyfile, re.M))
        self.assertEqual(sites, set(self.config["services"]))
        for name, service in self.config["services"].items():
            block = re.search(rf"^{name}\.\{{\$DOMAIN\}} \{{\n(.*?)^\}}", caddyfile, re.M | re.S)
            if "tailnet" in service:
                self.assertIn(f"import ts_upstream {service['tailnet']}\n", block.group(1))
            else:
                self.assertIn("{$%s_UPSTREAM}" % registry.env_name(name), block.group(1))

    def test_every_caddyfile_variable_is_generated_for_every_proxy(self):
        caddyfile = (ROOT / "services/caddy/Caddyfile").read_text()
        used = set(re.findall(r"\{\$([A-Z_]+_UPSTREAM)[:}]", caddyfile))
        self.assertFalse(any(":" in m for m in re.findall(r"\{\$[A-Z_]+_UPSTREAM(:[^}]*)\}",
                                                           caddyfile)), "defaults hide drift")
        proxy = self.config["proxy"]
        for host in (None, proxy["primary"], proxy["standby"]):
            self.assertEqual(used, set(registry.upstreams(self.config, host)), host)

    def test_host_compose_files_load_their_generated_upstreams(self):
        proxy = self.config["proxy"]
        for host in (proxy["primary"], proxy["standby"]):
            compose = (ROOT / f"docker-compose.{host}.yml").read_text()
            self.assertIn(f"services/caddy/generated/{host}.env", compose)
            self.assertNotIn("_UPSTREAM", compose)

    def test_command_line_outputs(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "checks", "failover"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line.split("\t") for line in result.stdout.splitlines()]
        self.assertEqual(lines[0], ["dashboard", "/", ""])
        self.assertIn(["gym-mcp", "/mcp", "optional"], lines)
        self.assertEqual(subprocess.run([sys.executable, str(SCRIPT), "generate", "--check"],
                                        capture_output=True, text=True).returncode, 0)

    def test_dashboard_data_covers_status_probes(self):
        data = json.loads((ROOT / "services/caddy/www/src/services.json").read_text())
        self.assertEqual({s["service"] for s in data["services"]},
                         set(registry.applications(self.config)))
        self.assertEqual(data["hosts"], self.config["hosts"])


if __name__ == "__main__":
    unittest.main()
