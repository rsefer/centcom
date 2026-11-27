import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-freshrss-extensions.sh"
MANIFEST = Path(__file__).resolve().parents[1] / "services/freshrss/extensions.conf"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, env=dict(os.environ, GIT_AUTHOR_NAME="t",
                          GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                          GIT_COMMITTER_EMAIL="t@t")).stdout.strip()


class InstallExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        (self.repo / "xExtension-Demo").mkdir(parents=True)
        (self.repo / "xExtension-Demo/metadata.json").write_text("{}")
        (self.repo / "xExtension-Demo/extension.php").write_text("v1")
        git(self.repo, "init", "-q")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-q", "-m", "one")
        self.sha1 = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "xExtension-Demo/extension.php").write_text("v2")
        git(self.repo, "commit", "-q", "-am", "two")
        self.sha2 = git(self.repo, "rev-parse", "HEAD")
        self.dest = self.root / "extensions"
        self.manifest = self.root / "extensions.conf"

    def install(self, line):
        self.manifest.write_text("# comment\n\n" + line + "\n")
        return subprocess.run(
            ["sh", str(SCRIPT)], capture_output=True, text=True,
            env=dict(os.environ, EXTENSIONS_MANIFEST=str(self.manifest),
                     EXTENSIONS_DIR=str(self.dest)))

    def test_installs_pinned_commit_and_upgrades_on_new_pin(self):
        result = self.install(f"file://{self.repo} {self.sha1} xExtension-Demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        ext = self.dest / "xExtension-Demo"
        self.assertEqual((ext / "extension.php").read_text(), "v1")
        self.assertFalse((ext / ".git").exists())
        result = self.install(f"file://{self.repo} {self.sha2} xExtension-Demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((ext / "extension.php").read_text(), "v2")

    def test_latest_tracks_default_branch(self):
        line = f"file://{self.repo} latest xExtension-Demo"
        self.assertEqual(self.install(line).returncode, 0)
        ext = self.dest / "xExtension-Demo"
        self.assertEqual((ext / "extension.php").read_text(), "v2")
        (self.repo / "xExtension-Demo/extension.php").write_text("v3")
        git(self.repo, "commit", "-q", "-am", "three")
        self.assertEqual(self.install(line).returncode, 0)
        self.assertEqual((ext / "extension.php").read_text(), "v3")
        self.assertEqual(self.install(line).returncode, 0)

    def test_unreachable_repo_keeps_installed_copy(self):
        self.install(f"file://{self.repo} latest xExtension-Demo")
        result = self.install(f"file://{self.root}/missing latest xExtension-Demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.dest / "xExtension-Demo/extension.php").read_text(), "v2")

    def test_unwritable_extensions_dir_is_handed_over_via_docker(self):
        if os.geteuid() == 0:
            self.skipTest("root can write anywhere")
        root = self.root / "host"
        (root / "scripts").mkdir(parents=True)
        (root / "services/freshrss").mkdir(parents=True)
        script = root / "scripts/install-freshrss-extensions.sh"
        script.write_text(SCRIPT.read_text())
        ext = root / "extensions"
        ext.mkdir()
        ext.chmod(0o555)
        self.addCleanup(ext.chmod, 0o755)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        docker = bin_dir / "docker"
        docker.write_text(
            '#!/bin/sh\necho "$*" >> "$TEST_LOG"\nchmod 755 "$TEST_EXT"\n')
        docker.chmod(0o755)
        (root / "services/freshrss/extensions.conf").write_text(
            f"file://{self.repo} {self.sha1} xExtension-Demo\n")
        log = root / "docker.log"
        result = subprocess.run(
            ["sh", str(script)], capture_output=True, text=True,
            env=dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
                     TEST_LOG=str(log), TEST_EXT=str(ext)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("exec -T -u root rss chown", log.read_text())
        self.assertTrue((ext / "xExtension-Demo/extension.php").exists())

    def test_unchanged_pin_is_not_reinstalled(self):
        line = f"file://{self.repo} {self.sha1} xExtension-Demo"
        self.install(line)
        (self.dest / "xExtension-Demo/local-marker").write_text("x")
        result = self.install(line)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already at", result.stdout)
        self.assertTrue((self.dest / "xExtension-Demo/local-marker").exists())

    def test_unlisted_extensions_are_left_alone(self):
        (self.dest / "xExtension-Manual").mkdir(parents=True)
        self.install(f"file://{self.repo} {self.sha1} xExtension-Demo")
        self.assertTrue((self.dest / "xExtension-Manual").is_dir())

    def test_removing_a_manifest_line_uninstalls_only_managed_extensions(self):
        (self.dest / "xExtension-Manual").mkdir(parents=True)
        self.install(f"file://{self.repo} {self.sha1} xExtension-Demo")
        self.assertTrue((self.dest / "xExtension-Demo").is_dir())
        self.manifest.write_text("# nothing\n")
        result = subprocess.run(
            ["sh", str(SCRIPT)], capture_output=True, text=True,
            env=dict(os.environ, EXTENSIONS_MANIFEST=str(self.manifest),
                     EXTENSIONS_DIR=str(self.dest)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.dest / "xExtension-Demo").exists())
        self.assertTrue((self.dest / "xExtension-Manual").is_dir())

    def test_failed_line_prevents_pruning(self):
        self.install(f"file://{self.repo} {self.sha1} xExtension-Demo")
        result = self.install("not-a-valid-line")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.dest / "xExtension-Demo").is_dir())

    def test_rejects_branch_names_and_non_extensions(self):
        result = self.install(f"file://{self.repo} main xExtension-Demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not 'latest' or a full commit SHA", result.stderr)
        result = self.install(f"file://{self.repo} {self.sha1} nope")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.dest / "nope").exists())

    def test_committed_manifest_is_well_formed(self):
        entries = [l.split() for l in MANIFEST.read_text().splitlines()
                   if l.strip() and not l.startswith("#")]
        for entry in entries:
            self.assertEqual(len(entry), 3, entry)
            self.assertRegex(entry[1], r"^(latest|[0-9a-f]{40})$")


if __name__ == "__main__":
    unittest.main()
