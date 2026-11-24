import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "self-heal.sh"
RECORD = 'echo "$(basename "$0") $*" >> "$CALLS"'

STUBS = {
    "ping": """host=""
for a; do host="$a"; done
case "$host" in
  192.168.4.1) [ -e "$STUBS/fail_gw" ] && exit 1;;
  1.1.1.1|9.9.9.9) [ -e "$STUBS/fail_wan" ] && exit 1;;
esac
exit 0""",
    "ip": """if [ "$*" = "-4 route show default" ]; then
  echo "default via 192.168.4.1 dev eth0 proto dhcp metric 100"
else
  %s
fi""" % RECORD,
    "tailscale": """if [ "$1 $2" = "status --json" ]; then
  if [ -e "$STUBS/ts_down" ]; then
    printf '{"BackendState": "Running", "Self": {"Online": false}}'
  else
    printf '{"BackendState": "Running", "Self": {"Online": true}}'
  fi
else
  echo "100.64.0.1 eastcom"
fi""",
    "docker": """case "$1" in
  ps) [ -e "$STUBS/unhealthy" ] && cat "$STUBS/unhealthy"; exit 0;;
  restart) %s;;
esac""" % RECORD,
    "vcgencmd": 'if [ -e "$STUBS/throttled" ]; then cat "$STUBS/throttled"; else echo throttled=0x0; fi',
    "lsmod": 'echo "smsc95xx 24576 0"',
    "journalctl": 'echo "journal line"',
    "modprobe": RECORD,
    "systemctl": RECORD,
    "sleep": "exit 0",
    "sync": "exit 0",
}


class SelfHealTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.stubs = root / "stubs"
        self.state = root / "state"
        self.run_dir = root / "run"
        self.stubs.mkdir()
        for name, body in STUBS.items():
            path = self.stubs / name
            path.write_text("#!/bin/sh\n" + body + "\n")
            path.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": "%s:%s:%s" % (self.stubs, Path(sys.executable).parent, os.environ["PATH"]),
            "STUBS": str(self.stubs),
            "CALLS": str(root / "calls.log"),
            "SELFHEAL_STATE_DIR": str(self.state),
            "SELFHEAL_RUN_DIR": str(self.run_dir),
        }

    def flag(self, name, content=""):
        (self.stubs / name).write_text(content)

    def run_script(self, times=1, **env):
        for _ in range(times):
            subprocess.run(["sh", str(SCRIPT)], env={**self.env, **env}, check=True, capture_output=True)

    def calls(self):
        path = Path(self.env["CALLS"])
        return path.read_text().splitlines() if path.exists() else []

    def events(self):
        path = self.state / "events.log"
        return path.read_text() if path.exists() else ""

    def test_syntax_is_valid_posix_sh(self):
        subprocess.run(["sh", "-n", str(SCRIPT)], check=True)

    def test_healthy_host_does_nothing(self):
        self.run_script(3)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events(), "")

    def test_gateway_outage_escalates_to_reboot(self):
        self.flag("fail_gw")
        self.flag("fail_wan")
        self.run_script(2)
        self.assertEqual(self.calls(), [])
        self.run_script(1)
        self.assertEqual(self.calls(), ["ip link set eth0 down", "ip link set eth0 up"])
        self.run_script(3)
        self.assertIn("modprobe -r smsc95xx", self.calls())
        self.assertIn("modprobe smsc95xx", self.calls())
        self.assertNotIn("systemctl reboot", self.calls())
        self.run_script(5)
        self.assertNotIn("systemctl reboot", self.calls())
        self.run_script(1)
        self.assertEqual(self.calls()[-1], "systemctl reboot")
        self.assertEqual((self.state / "reboot_attempts").read_text(), "1")
        self.assertEqual(len(list(self.state.glob("diag-*.log"))), 1)
        self.assertIn("rebooting: gateway down for 12 min", self.events())

    def test_wan_outage_is_logged_but_never_acted_on(self):
        self.flag("fail_wan")
        self.run_script(40)
        self.assertEqual(self.calls(), [])
        self.assertIn("connectivity: none -> wan", self.events())

    def test_tailscale_outage_restarts_tailscaled_first(self):
        self.flag("ts_down")
        self.run_script(3)
        self.assertEqual(self.calls(), ["systemctl restart tailscaled"])
        self.run_script(3)
        self.assertEqual(self.calls()[1:], ["ip link set eth0 down", "ip link set eth0 up", "systemctl restart tailscaled"])

    def test_reboot_is_suppressed_after_max_attempts(self):
        self.state.mkdir()
        (self.state / "reboot_attempts").write_text("3")
        self.flag("fail_gw")
        self.flag("fail_wan")
        self.run_script(12)
        self.assertNotIn("systemctl reboot", self.calls())
        self.assertIn("reboot suppressed", self.events())

    def test_healthy_streak_resets_reboot_attempts_and_logs_recovery(self):
        self.state.mkdir()
        (self.state / "reboot_attempts").write_text("2")
        self.flag("fail_gw")
        self.flag("fail_wan")
        self.run_script(1)
        (self.stubs / "fail_gw").unlink()
        (self.stubs / "fail_wan").unlink()
        self.run_script(10)
        self.assertEqual((self.state / "reboot_attempts").read_text(), "0")
        self.assertIn("recovered after 1 failed minutes", self.events())

    def test_container_restarts_after_five_unhealthy_checks(self):
        self.flag("unhealthy", "beszel-agent\n")
        self.run_script(4)
        self.assertEqual(self.calls(), [])
        self.run_script(1)
        self.assertEqual(self.calls(), ["docker restart beszel-agent"])

    def test_container_counter_clears_when_healthy_again(self):
        self.flag("unhealthy", "beszel-agent\n")
        self.run_script(4)
        (self.stubs / "unhealthy").unlink()
        self.run_script(1)
        self.assertEqual(list(self.run_dir.glob("unhealthy.*")), [])

    def test_power_flags_are_logged_once_per_change(self):
        self.flag("throttled", "throttled=0x50005\n")
        self.run_script(3)
        self.assertEqual(self.events().count("get_throttled=0x50005"), 1)

    def test_dry_run_changes_nothing(self):
        self.flag("fail_gw")
        self.flag("fail_wan")
        self.run_script(12, SELFHEAL_DRY_RUN="1")
        self.assertEqual([c for c in self.calls() if c not in ("ip -br addr", "ip route")], [])
        self.assertIn("dry-run: systemctl reboot", self.events())
        self.assertFalse((self.state / "reboot_attempts").exists())


if __name__ == "__main__":
    unittest.main()
