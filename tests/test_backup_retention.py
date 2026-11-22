import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest


BACKUP_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"


class BackupRetentionTests(unittest.TestCase):
    def run_incomplete_backup(self, services, docker_failure):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script_dir = root / "scripts"
            script_dir.mkdir()
            shutil.copy(BACKUP_SCRIPT, script_dir / "backup.sh")
            (root / "data").mkdir()
            (root / "extensions").mkdir()
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "docker.log"
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
                'if [ "$1 $2 $3" = "compose config --services" ]; then\n'
                f'  printf "{services}\\n"\n'
                "  exit 0\n"
                "fi\n"
                + docker_failure + "\n"
                "exit 0\n"
            )
            docker.chmod(0o755)
            backups = root / "backups"
            old = backups / "20200101-030000"
            old.mkdir(parents=True)
            old_time = time.time() - 101 * 86400
            os.utime(old, (old_time, old_time))
            env = dict(os.environ, HOME=temp, BACKUP_ROOT=str(backups),
                       BACKUP_S3_BUCKET="backup-bucket", DOCKER_LOG=str(log),
                       PATH=f"{bin_dir}:{os.environ['PATH']}")
            result = subprocess.run(["sh", str(script_dir / "backup.sh")], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(old.exists())
            self.assertNotIn("amazon/aws-cli", log.read_text())
            self.assertNotIn("centcom_status_data", log.read_text())
            return result

    def test_missing_volume_does_not_create_or_upload_an_empty_backup(self):
        result = self.run_incomplete_backup(
            "caddy", '[ "$1 $2" != "volume inspect" ] || exit 1')
        self.assertIn("Missing or inaccessible volume for caddy_data", result.stderr)

    def test_stopped_freshrss_does_not_upload_a_stale_opml_backup(self):
        result = self.run_incomplete_backup("rss", "")
        self.assertIn("FreshRSS is not running", result.stderr)

    def test_failed_opml_export_does_not_upload_an_incomplete_backup(self):
        result = self.run_incomplete_backup(
            "rss",
            'if [ "$1 $2" = "compose ps" ]; then echo running; fi\n'
            '[ "$1 $2" != "compose exec" ] || exit 1',
        )
        self.assertIn("Failed to export FreshRSS OPML", result.stderr)

    def test_multi_host_backup_uses_hostname_in_s3_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script_dir = root / "scripts"
            script_dir.mkdir()
            shutil.copy(BACKUP_SCRIPT, script_dir / "backup.sh")
            (root / "compose.yaml").symlink_to("docker-compose.northcom.yml")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            for name, body in {"docker": "exit 0\n", "hostname": "echo northcom\n"}.items():
                command = bin_dir / name
                command.write_text("#!/bin/sh\n" + body)
                command.chmod(0o755)
            env = dict(os.environ, HOME=temp, BACKUP_ROOT=str(root / "backups"),
                       BACKUP_S3_BUCKET="backup-bucket", BACKUP_S3_PREFIX="centcom",
                       PATH=f"{bin_dir}:{os.environ['PATH']}")
            result = subprocess.run(
                ["sh", str(script_dir / "backup.sh")], env=env,
                capture_output=True, text=True, check=True,
            )
            self.assertIn("s3://backup-bucket/centcom/northcom/", result.stdout)

    def test_backup_only_archives_services_on_this_host(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script_dir = root / "scripts"
            script_dir.mkdir()
            shutil.copy(BACKUP_SCRIPT, script_dir / "backup.sh")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/bin/sh\n"
                'if [ "$1 $2 $3" = "compose config --services" ]; then\n'
                '  printf "caddy\\npihole\\nbeszel-agent\\n"\n'
                "fi\n"
                "exit 0\n"
            )
            docker.chmod(0o755)
            env = dict(os.environ, HOME=temp, BACKUP_ROOT=str(root / "backups"),
                       PATH=f"{bin_dir}:{os.environ['PATH']}")
            env.pop("BACKUP_S3_BUCKET", None)
            result = subprocess.run(
                ["sh", str(script_dir / "backup.sh")], env=env,
                capture_output=True, text=True, check=True,
            )
            self.assertIn("Backing up pihole_data", result.stdout)
            self.assertIn("Backing up caddy_data", result.stdout)
            self.assertIn("Backing up beszel_agent_data", result.stdout)
            self.assertNotIn("Backing up opengym_data", result.stdout)
            self.assertNotIn("Backing up beszel_data", result.stdout)

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
