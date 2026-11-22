from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "harden-host.sh"


class HardenHostTests(unittest.TestCase):
    def test_syntax_is_valid_posix_sh(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_sets_watchdog_ntp_and_log2ram(self):
        text = SCRIPT.read_text()
        for expected in ("RuntimeWatchdogSec=15", "timedatectl set-ntp true", "install -y log2ram"):
            self.assertIn(expected, text)


if __name__ == "__main__":
    unittest.main()
