import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
HOST = subprocess.check_output(["hostname", "-s"], text=True).strip()


class BackupAlertTests(unittest.TestCase):
    def run_backup(self, ping_url="", fail=False, curl_fails=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "scripts").mkdir()
        shutil.copy(BACKUP_SCRIPT, root / "scripts" / "backup.sh")
        if ping_url:
            (root / ".env").write_text(f"BACKUP_PING_URL={ping_url}\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.log = root / "calls.log"
        stubs = {
            # A missing volume makes the caddy backup fail.
            "docker": 'if [ "$1 $2 $3" = "compose config --services" ]; then\n'
                      f'  printf "{"caddy" if fail else ""}\\n"; exit 0\nfi\n'
                      '[ "$1 $2" != "volume inspect" ] || exit 1\nexit 0',
            "curl": 'printf "curl %s\\n" "$*" >> "$CALL_LOG"\n' + ("exit 22" if curl_fails else "exit 0"),
            "python3": 'printf "python3 %s\\n%s\\n" "$*" "$(cat)" >> "$CALL_LOG"',
        }
        for name, body in stubs.items():
            stub = bin_dir / name
            stub.write_text("#!/bin/sh\n" + body + "\n")
            stub.chmod(0o755)
        self.backups = root / "backups"
        env = dict(os.environ, HOME=temp.name, BACKUP_ROOT=str(self.backups),
                   CALL_LOG=str(self.log), PATH=f"{bin_dir}:{os.environ['PATH']}")
        env.pop("BACKUP_PING_URL", None)
        result = subprocess.run(["sh", str(root / "scripts" / "backup.sh")], env=env,
                                capture_output=True, text=True)
        self.calls = self.log.read_text() if self.log.exists() else ""
        return result

    def test_success_records_the_time_and_pings_once_without_an_email(self):
        result = self.run_backup("https://ping.example/key/backup-{host}?create=1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex((self.backups / ".last-success").read_text(),
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$")
        self.assertEqual(self.calls.splitlines(), [
            f"curl -fsS -m 10 --retry 3 -o /dev/null https://ping.example/key/backup-{HOST}?create=1"])

    def test_failure_pings_fail_before_the_query_and_emails(self):
        result = self.run_backup("https://ping.example/key/backup-{host}?create=1", fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.backups / ".last-success").exists())
        self.assertIn(f"https://ping.example/key/backup-{HOST}/fail?create=1", self.calls)
        self.assertIn(f"[centcom] backup FAILED on {HOST}", self.calls)
        self.assertIn("Failed parts: caddy_data", self.calls)

    def test_failure_without_a_ping_url_still_emails(self):
        result = self.run_backup(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("curl", self.calls)
        self.assertIn("backup FAILED", self.calls)

    def test_no_ping_url_means_no_ping(self):
        self.assertEqual(self.run_backup().returncode, 0)
        self.assertEqual(self.calls, "")

    def test_a_failing_ping_does_not_fail_the_backup(self):
        result = self.run_backup("https://ping.example/uuid", curl_fails=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Backup ping to https://ping.example/uuid failed", result.stderr)


if __name__ == "__main__":
    unittest.main()
