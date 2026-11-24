from pathlib import Path
import subprocess
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
PROVISION = SCRIPTS / "provision-host.sh"
NEW_HOST = SCRIPTS / "new-host.sh"


def run(script, *args):
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True)


class ProvisionHostTests(unittest.TestCase):
    def test_syntax_is_valid_posix_sh(self):
        for script in (PROVISION, NEW_HOST):
            subprocess.run(["sh", "-n", str(script)], check=True)

    def test_provision_requires_a_host(self):
        result = run(PROVISION)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)

    def test_provision_rejects_unknown_option_and_bad_host(self):
        self.assertIn("Usage:", run(PROVISION, "eastcom", "--bogus").stderr)
        result = run(PROVISION, "East_Com")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid host", result.stderr)

    def test_new_host_rejects_bad_arguments_before_any_network_access(self):
        self.assertIn("Usage:", run(NEW_HOST).stderr)
        result = run(NEW_HOST, "../x")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid host", result.stderr)

    def test_new_host_refuses_a_host_without_a_compose_file(self):
        result = run(NEW_HOST, "nowhere")
        self.assertNotEqual(result.returncode, 0)

    def test_provision_covers_every_setup_step(self):
        text = PROVISION.read_text()
        for expected in (
            "hostnamectl set-hostname",
            "ln -sfn",
            "setup.sh --bootstrap-only",
            "--advertise-tags=tag:centcom",
            "configure-routing.sh",
            "harden-host.sh",
            "setup-cron.sh",
            "reload.sh",
        ):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
