import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.sh"


class BootstrapTests(unittest.TestCase):
    def test_existing_install_does_not_deploy_or_configure_cron(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "sudo.log"
            for name, body in {
                "sudo": 'echo "$*" >> "$LOG"\n',
                "docker": "exit 0\n",
                "tailscale": "exit 0\n",
                "getent": "exit 0\n",
                "id": 'echo "user docker"\n',
            }.items():
                command = root / name
                command.write_text("#!/bin/sh\n" + body)
                command.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}:/usr/bin:/bin", LOG=str(log))
            result = subprocess.run(
                ["sh", str(SCRIPT), "--bootstrap-only"],
                env=env, capture_output=True, text=True, check=True,
            )
            commands = log.read_text()
            self.assertIn("docker info", commands)
            self.assertNotIn("upgrade", commands)
            self.assertNotIn("sysctl", commands)
            self.assertNotIn("network create", commands)
            self.assertIn("sudo tailscale up", result.stdout)

    def test_unknown_option_fails_before_setup(self):
        result = subprocess.run(
            ["sh", str(SCRIPT), "--invalid"], capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
