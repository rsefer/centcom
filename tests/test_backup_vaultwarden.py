import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup-vaultwarden.sh"
HOST = subprocess.check_output(["hostname", "-s"], text=True).strip()
KEY = "age1" + "q" * 58
OTHER_KEY = "age1" + "p" * 58

DOCKER = r"""#!/bin/sh
printf 'docker %s\n' "$*" >> "$CALL_LOG"
case "$*" in
  "compose config --services") printf '%s\n' "${STUB_SERVICES:-vaultwarden}" ;;
  *"alpine sh"*)
    case "${STUB_VAULT:-ok}" in
      ok) printf 'age-encryption.org/v1\n'; head -c 300 /dev/zero ;;
      bad) echo "not encrypted" ;;
      fail) printf 'age-enc'; exit 1 ;;
    esac ;;
  *aws-cli*) [ "${STUB_S3_FAIL:-}" != 1 ] || exit 1 ;;
esac
"""


class VaultwardenBackupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(SCRIPT, self.root / "scripts" / "backup-vaultwarden.sh")
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        for name, body in {"docker": DOCKER,
                           "python3": '#!/bin/sh\nprintf "python3 %s\\n%s\\n" "$*" "$(cat)" >> "$CALL_LOG"\n'}.items():
            stub = bin_dir / name
            stub.write_text(body)
            stub.chmod(0o755)
        self.log = self.root / "calls.log"
        self.backups = self.root / "backups"
        self.vault = self.backups / "vaultwarden"
        self.env = dict(os.environ, HOME=temp.name, BACKUP_ROOT=str(self.backups),
                        CALL_LOG=str(self.log), PATH=f"{bin_dir}:{os.environ['PATH']}")
        for key in ("VAULTWARDEN_AGE_RECIPIENT", "BACKUP_S3_BUCKET", "BACKUP_S3_PREFIX",
                    "VAULTWARDEN_BACKUP_KEEP", "STUB_VAULT", "STUB_SERVICES", "STUB_S3_FAIL"):
            self.env.pop(key, None)

    def run_backup(self, **env):
        result = subprocess.run(["sh", str(self.root / "scripts" / "backup-vaultwarden.sh")],
                                env=dict(self.env, **env), capture_output=True, text=True)
        self.calls = self.log.read_text() if self.log.exists() else ""
        return result

    def archives(self):
        return sorted(p.name for p in self.vault.glob("vaultwarden-*.tar.gz.age")) if self.vault.exists() else []

    def test_does_nothing_without_a_recipient_key(self):
        result = self.run_backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VAULTWARDEN_AGE_RECIPIENT is not set", result.stdout)
        self.assertNotIn("alpine", self.calls)

    def test_does_nothing_on_a_host_without_vaultwarden(self):
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, STUB_SERVICES="caddy")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("alpine", self.calls)

    def test_rejects_anything_that_is_not_a_public_key_and_emails(self):
        for bad in ("AGE-SECRET-KEY-1ABCDEF", "hunter2", "age1", "age1abc!def"):
            result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("age public keys", result.stderr)
        self.assertNotIn("alpine", self.calls)
        self.assertIn("Vaultwarden backup FAILED", self.calls)

    def test_writes_a_private_encrypted_file_and_records_success(self):
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=f"{KEY}, {OTHER_KEY}")
        self.assertEqual(result.returncode, 0, result.stderr)
        (name,) = self.archives()
        self.assertRegex(name, rf"^vaultwarden-{HOST}-\d{{8}}-\d{{6}}\.tar\.gz\.age$")
        self.assertEqual(stat.S_IMODE((self.vault / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.vault.stat().st_mode), 0o700)
        self.assertRegex((self.vault / ".last-success").read_text(), r"^\d{4}-\d\d-\d\dT")
        self.assertEqual([p.name for p in self.vault.glob(".*.partial")], [])
        self.assertIn(f"-e RECIPIENTS={KEY} {OTHER_KEY}", self.calls)
        self.assertIn("-v centcom_vaultwarden_data:/data", self.calls)
        self.assertNotIn("aws-cli", self.calls)
        self.assertNotIn("FAILED", self.calls)

    def test_uploads_under_the_host_prefix_on_a_multi_host_checkout(self):
        (self.root / "compose.yaml").symlink_to("docker-compose.southcom.yml")
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, BACKUP_S3_BUCKET="bucket",
                                 VAULTWARDEN_VOLUME="custom_volume")
        self.assertEqual(result.returncode, 0, result.stderr)
        (name,) = self.archives()
        self.assertIn(f"s3 cp /backup/{name} s3://bucket/centcom/{HOST}/vaultwarden/{name}", self.calls)
        self.assertIn("-v custom_volume:/data", self.calls)

    def test_keeps_only_the_newest_files(self):
        self.vault.mkdir(parents=True)
        for number in range(5):
            old = self.vault / f"vaultwarden-{HOST}-2026010{number}-000000.tar.gz.age"
            old.write_text("old")
            stamp = time.time() - (10 - number) * 86400
            os.utime(old, (stamp, stamp))
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, VAULTWARDEN_BACKUP_KEEP="3")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = self.archives()
        self.assertEqual(len(names), 3)
        self.assertEqual(names[:2], [f"vaultwarden-{HOST}-20260103-000000.tar.gz.age",
                                     f"vaultwarden-{HOST}-20260104-000000.tar.gz.age"])

    def test_output_that_is_not_an_age_file_fails_and_leaves_nothing_behind(self):
        for mode in ("bad", "fail"):
            result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, STUB_VAULT=mode)
            self.assertNotEqual(result.returncode, 0, mode)
            self.assertEqual(self.archives(), [])
            self.assertEqual([p.name for p in self.vault.glob(".*")], [])
        self.assertIn("Vaultwarden backup FAILED", self.calls)

    def test_a_failed_upload_fails_without_recording_success(self):
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, BACKUP_S3_BUCKET="bucket", STUB_S3_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.vault / ".last-success").exists())
        self.assertIn("Vaultwarden backup FAILED", self.calls)

    def test_invalid_retention_is_rejected(self):
        result = self.run_backup(VAULTWARDEN_AGE_RECIPIENT=KEY, VAULTWARDEN_BACKUP_KEEP="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("alpine", self.calls)


if __name__ == "__main__":
    unittest.main()
