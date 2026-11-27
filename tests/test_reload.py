import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        for name in ("reload.sh", "services.py", "setup-cron.sh", "boot-recover.sh"):
            shutil.copy(ROOT / "scripts" / name, self.root / "scripts" / name)
        shutil.copy(ROOT / "services.toml", self.root / "services.toml")
        (self.root / "services/caddy/www/src").mkdir(parents=True)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        commands = {
            "tailscale": 'printf "100.1.1.1\\n"\n',
            "crontab": (
                'printf "%s\\n" '
                '"~/centcom/scripts/boot-recover.sh >> ~/centcom/boot-recover.log 2>&1" '
                '"~/centcom/scripts/backup.sh >> ~/centcom/backup.log 2>&1"\n'
            ),
            "docker": (
                'if [ "$1" = compose ]; then\n'
                '  test -f services/caddy/generated/routes.caddy || exit 1\n'
                '  test -f services/caddy/generated/northcom.env || exit 1\n'
                '  test -f services/caddy/generated/northcom.compose.yaml || exit 1\n'
                '  test -f services/caddy/generated/status.env || exit 1\n'
                '  test -f services/caddy/www/src/services.json || exit 1\n'
                'fi\n'
                'printf "%s\\n" "$*" >> "$TEST_LOG"\n'
                'case "$*" in\n'
                '  "compose config --services") printf "caddy\\n";;\n'
                '  "compose ps -q caddy") printf "caddy-id\\n";;\n'
                '  inspect*) printf "healthy\\n";;\n'
                'esac\n'
            ),
        }
        for name, body in commands.items():
            command = self.bin / name
            command.write_text("#!/bin/sh\nset -eu\n" + body)
            command.chmod(0o755)
        self.log = self.root / "docker.log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_LOG=str(self.log))
        self.env.pop("SERVICES_FILE", None)

    def reload(self):
        return subprocess.run(["sh", str(self.root / "scripts/reload.sh")],
                              env=self.env, capture_output=True, text=True)

    def test_cron_setup_generates_before_reading_compose(self):
        result = subprocess.run(["bash", str(self.root / "scripts/setup-cron.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), ["compose config --services"])

    def test_boot_recovery_generates_before_starting_compose(self):
        result = subprocess.run(["sh", str(self.root / "scripts/boot-recover.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines()[-1],
                         "compose up -d --remove-orphans")

    def test_generates_before_docker_and_recreates_caddy(self):
        result = self.reload()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertEqual(commands[:3], ["compose pull --ignore-buildable",
                                       "compose build", "compose up -d --remove-orphans"])
        self.assertIn("compose up -d --no-deps --force-recreate caddy", commands)
        routes = (self.root / "services/caddy/generated/routes.caddy").read_text()
        self.assertIn("gym.{$DOMAIN}", routes)
        self.assertNotIn("gym-mcp", routes)

    def test_rollback_skips_image_pull(self):
        self.env["RELOAD_SKIP_PULL"] = "1"
        result = self.reload()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.log.read_text().splitlines()
        self.assertEqual(commands[:2], ["compose build", "compose up -d --remove-orphans"])
        self.assertNotIn("compose pull --ignore-buildable", commands)

    def test_generation_failure_stops_before_docker(self):
        (self.root / "services.toml").write_text('hosts = []\n')
        result = self.reload()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hosts must be", result.stderr)
        self.assertFalse(self.log.exists())

    def test_cron_setup_generation_failure_stops_before_compose(self):
        (self.root / "services.toml").write_text('hosts = []\n')
        result = subprocess.run(["bash", str(self.root / "scripts/setup-cron.sh")],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hosts must be", result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
