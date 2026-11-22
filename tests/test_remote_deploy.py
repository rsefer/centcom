import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


DEPLOY_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "remote-deploy.sh"
COMMIT_MESSAGE = "Fixture\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"


class RemoteDeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        (self.work / "scripts").mkdir()
        shutil.copy(DEPLOY_SCRIPT, self.work / "scripts" / "remote-deploy.sh")
        for name in ("setup-cron.sh", "reload.sh"):
            script = self.work / "scripts" / name
            script.write_text('#!/bin/sh\nprintf "%s %s\\n" "$TEST_HOST" "' + name +
                              '" >> "$TEST_LOG"\n')
            script.chmod(0o755)
        (self.work / "scripts" / "check-services.py").write_text(
            "import os\n"
            "with open(os.environ['TEST_LOG'], 'a') as log:\n"
            "    log.write(os.environ['TEST_HOST'] + ' check-services.py\\n')\n"
            "raise SystemExit(int(os.environ.get('CHECK_FAIL', '0')))\n"
        )
        for host in ("northcom", "southcom"):
            (self.work / f"docker-compose.{host}.yml").write_text("services: {}\n")
        (self.work / ".gitignore").write_text("compose.yaml\n")
        self.git("add", ".")
        self.git("commit", "-m", COMMIT_MESSAGE)
        self.origin = self.root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.origin)],
                       check=True, stdout=subprocess.DEVNULL)
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-u", "origin", "main")
        self.homes = self.root / "homes"
        for host in ("northcom", "southcom"):
            home = self.homes / host
            home.mkdir(parents=True)
            subprocess.run(["git", "clone", "-q", str(self.origin), str(home / "centcom")],
                           check=True)
            (home / "centcom" / "compose.yaml").symlink_to(f"docker-compose.{host}.yml")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        ssh = self.bin / "ssh"
        ssh.write_text(
            "#!/bin/sh\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  case \"$1\" in northcom.local|southcom.local) break;; esac\n"
            "  shift\n"
            "done\n"
            'host="${1%.local}"\n'
            "shift\n"
            'printf "connect %s\\n" "$host" >> "$TEST_LOG"\n'
            '[ "$host" != "${FAIL_HOST:-}" ] || exit 1\n'
            'HOME="$TEST_HOMES/$host" TEST_HOST="$host" "$@"\n'
        )
        ssh.chmod(0o755)
        systemctl = self.bin / "systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n")
        systemctl.chmod(0o755)
        self.log = self.root / "log"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        TEST_LOG=str(self.log), TEST_HOMES=str(self.homes))

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.work, check=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def deploy(self, **env):
        return subprocess.run(["sh", "scripts/remote-deploy.sh"], cwd=self.work,
                              env=dict(self.env, **env), capture_output=True, text=True)

    def test_discovers_hosts_and_deploys_after_all_preflights(self):
        with (self.work / "docker-compose.northcom.yml").open("a") as compose:
            compose.write("# updated\n")
        self.git("add", "docker-compose.northcom.yml")
        self.git("commit", "-m", COMMIT_MESSAGE)
        self.git("push", "origin", "main")
        expected = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                           cwd=self.work, text=True).strip()
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
            "connect northcom", "northcom setup-cron.sh", "northcom reload.sh",
            "connect southcom", "southcom setup-cron.sh", "southcom reload.sh",
            "connect southcom", "southcom check-services.py",
        ])
        for host in ("northcom", "southcom"):
            actual = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=self.homes / host / "centcom", text=True,
            ).strip()
            self.assertEqual(actual, expected)

    def test_failed_service_check_fails_deployment(self):
        result = self.deploy(CHECK_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("southcom check-services.py", self.log.read_text())

    def test_dirty_host_prevents_all_deployments(self):
        (self.homes / "southcom" / "centcom" / "unexpected.txt").write_text("local")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
        ])

    def test_unpublished_local_commit_prevents_deployment(self):
        (self.work / "new-file").write_text("not published")
        self.git("add", "new-file")
        self.git("commit", "-m", COMMIT_MESSAGE)
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not on GitHub", result.stderr)
        self.assertFalse(self.log.exists())

    def test_unreachable_host_prevents_all_deployments(self):
        result = self.deploy(FAIL_HOST="southcom")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
        ])

    def test_incorrect_compose_selection_prevents_all_deployments(self):
        selection = self.homes / "southcom" / "centcom" / "compose.yaml"
        selection.unlink()
        selection.symlink_to("docker-compose.northcom.yml")
        result = self.deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compose.yaml does not select southcom", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
        ])


if __name__ == "__main__":
    unittest.main()
