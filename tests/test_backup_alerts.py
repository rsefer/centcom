import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
HOST = subprocess.check_output(["hostname", "-s"], text=True).strip()


class BackupAlertTests(unittest.TestCase):
    def run_backup(self, fail=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / "scripts").mkdir()
        shutil.copy(BACKUP_SCRIPT, root / "scripts" / "backup.sh")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self.log = root / "calls.log"
        stubs = {
            # A missing volume makes the caddy backup fail.
            "docker": 'if [ "$1 $2 $3" = "compose config --services" ]; then\n'
                      f'  printf "{"caddy" if fail else ""}\\n"; exit 0\nfi\n'
                      '[ "$1 $2" != "volume inspect" ] || exit 1\nexit 0',
            "python3": 'printf "python3 %s\\n%s\\n" "$*" "$(cat)" >> "$CALL_LOG"',
        }
        for name, body in stubs.items():
            stub = bin_dir / name
            stub.write_text("#!/bin/sh\n" + body + "\n")
            stub.chmod(0o755)
        self.backups = root / "backups"
        env = dict(os.environ, HOME=temp.name, BACKUP_ROOT=str(self.backups),
                   CALL_LOG=str(self.log), PATH=f"{bin_dir}:{os.environ['PATH']}")
        result = subprocess.run(["sh", str(root / "scripts" / "backup.sh")], env=env,
                                capture_output=True, text=True)
        self.calls = self.log.read_text() if self.log.exists() else ""
        return result

    def test_success_records_the_time_and_sends_no_email(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex((self.backups / ".last-success").read_text(),
                         r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\n$")
        self.assertEqual(self.calls, "")

    def test_failure_emails_with_the_failed_parts_and_records_no_success(self):
        result = self.run_backup(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.backups / ".last-success").exists())
        self.assertIn(f"[centcom] backup FAILED on {HOST}", self.calls)
        self.assertIn("Failed parts: caddy_data", self.calls)


if __name__ == "__main__":
    unittest.main()
