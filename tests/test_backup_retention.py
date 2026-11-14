import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"


class BackupRetentionTests(unittest.TestCase):
    def test_age_based_retention_preserves_recent_and_unrelated_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script_dir = root / "scripts"
            script_dir.mkdir()
            shutil.copy(BACKUP_SCRIPT, script_dir / "backup.sh")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text("#!/bin/sh\nexit 0\n")
            docker.chmod(0o755)
            backups = root / "backups"
            backups.mkdir()
            old = backups / "20200101-030000"
            old.mkdir()
            old_time = time.time() - 101 * 86400
            os.utime(old, (old_time, old_time))
            unrelated = backups / "other-files"
            unrelated.mkdir()
            os.utime(unrelated, (old_time, old_time))
            for i in range(101):
                (backups / f"20260101-{i:06d}").mkdir()
            recent = backups / "20260101-000000"
            recent_time = time.time() - 99 * 86400
            os.utime(recent, (recent_time, recent_time))

            env = dict(os.environ, HOME=temp, BACKUP_ROOT=str(backups),
                       PATH=f"{bin_dir}:{os.environ['PATH']}")
            env.pop("BACKUP_RETENTION", None)
            env.pop("BACKUP_S3_BUCKET", None)
            subprocess.run(["sh", str(script_dir / "backup.sh")], env=env, check=True,
                           stdout=subprocess.DEVNULL)

            self.assertFalse(old.exists())
            self.assertTrue(unrelated.exists())
            self.assertTrue(all((backups / f"20260101-{i:06d}").exists()
                                for i in range(101)))

    def test_invalid_retention_fails_before_creating_a_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            backups = Path(temp) / "backups"
            env = dict(os.environ, HOME=temp, BACKUP_ROOT=str(backups),
                       BACKUP_RETENTION="invalid")
            result = subprocess.run(["sh", str(BACKUP_SCRIPT)], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("positive whole number", result.stderr)
            self.assertFalse(backups.exists())


if __name__ == "__main__":
    unittest.main()
