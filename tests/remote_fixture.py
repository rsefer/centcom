import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
COMMIT_MESSAGE = "Fixture\n\nCo-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"


class RemoteHostsFixture(unittest.TestCase):
    """Two fake hosts cloned from a local origin, reached through a fake ssh."""

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
        for name in ("remote-deploy.sh", "remote-rollback.sh"):
            shutil.copy(SCRIPTS / name, self.work / "scripts" / name)
        for name in ("setup-cron.sh", "reload.sh"):
            script = self.work / "scripts" / name
            script.write_text('#!/bin/sh\nprintf "%s %s%s\\n" "$TEST_HOST" "' + name +
                              '" "${RELOAD_SKIP_PULL:+ skip-pull}" >> "$TEST_LOG"\n')
            script.chmod(0o755)
        backup = self.work / "scripts" / "backup.sh"
        backup.write_text('#!/bin/sh\nprintf "%s backup.sh\\n" "$TEST_HOST" >> "$TEST_LOG"\n'
                          '[ "$TEST_HOST" != "${BACKUP_FAIL_HOST:-}" ]\n')
        backup.chmod(0o755)
        (self.work / "scripts" / "check-services.py").write_text(
            "import os\n"
            "with open(os.environ['TEST_LOG'], 'a') as log:\n"
            "    log.write(os.environ['TEST_HOST'] + ' check-services.py\\n')\n"
            "raise SystemExit(int(os.environ.get('CHECK_FAIL', '0')))\n"
        )
        for host in ("northcom", "southcom"):
            (self.work / f"docker-compose.{host}.yml").write_text("services: {}\n")
        (self.work / ".gitignore").write_text("compose.yaml\n.env\n")
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
            # Like real ssh, join the arguments into one string for a remote shell
            # to parse, so an empty argument disappears.
            'HOME="$TEST_HOMES/$host" TEST_HOST="$host" sh -c "$*"\n'
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

    def publish(self, note):
        """Commit a change to northcom's Compose file, push it, and return the commit."""
        with (self.work / "docker-compose.northcom.yml").open("a") as compose:
            compose.write(f"# {note}\n")
        self.git("add", "docker-compose.northcom.yml")
        self.git("commit", "-m", COMMIT_MESSAGE)
        self.git("push", "origin", "main")
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=self.work, text=True).strip()

    def publish_file(self, name, text):
        """Commit a new version of a file, push it, and return the commit."""
        (self.work / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-m", COMMIT_MESSAGE)
        self.git("push", "origin", "main")

    def write_env(self, host, text):
        (self.homes / host / "centcom" / ".env").write_text(text)

    def head(self, host):
        return subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=self.homes / host / "centcom", text=True).strip()
