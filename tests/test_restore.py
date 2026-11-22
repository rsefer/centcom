import gzip
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


RESTORE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore.sh"


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "target"
        self.target.mkdir()
        (self.target / "existing").write_text("keep me")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "from pathlib import Path\n"
            "args = sys.argv[1:]\n"
            "root = Path(os.environ['TEST_ROOT'])\n"
            "stage = root / 'stage'\n"
            "if args[:2] == ['volume', 'create']:\n"
            "    stage.mkdir()\n"
            "    print('staging-volume')\n"
            "elif args[:2] == ['volume', 'rm']:\n"
            "    import shutil\n"
            "    shutil.rmtree(stage)\n"
            "elif args[0] == 'run':\n"
            "    cmd = args[args.index('-c') + 1]\n"
            "    cmd = cmd.replace('/staging', str(stage)).replace('/target', str(root / 'target'))\n"
            "    rest = args[args.index('-c') + 2:]\n"
            "    rest = [arg.replace('/backup/', str(root) + '/') for arg in rest]\n"
            "    sys.exit(subprocess.run(['sh', '-c', cmd, *rest]).returncode)\n"
            "else:\n"
            "    sys.exit(1)\n"
        )
        docker.chmod(0o755)
        self.env = dict(os.environ, TEST_ROOT=str(self.root),
                        PATH=f"{self.bin}:{os.environ['PATH']}")
        self.archive = self.root / "opengym_data.tar.gz"

    def restore(self):
        return subprocess.run(
            ["sh", str(RESTORE_SCRIPT), str(self.archive)], input="yes\n",
            env=self.env, capture_output=True, text=True,
        )

    def test_valid_archive_replaces_target(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            data = b"restored"
            info = tarfile.TarInfo("new")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        result = self.restore()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.target / "existing").exists())
        self.assertEqual((self.target / "new").read_text(), "restored")
        self.assertFalse((self.root / "stage").exists())

    def test_invalid_archive_preserves_target(self):
        for contents in (b"not gzip", gzip.compress(b"not a tar archive")):
            with self.subTest(contents=contents):
                self.archive.write_bytes(contents)
                result = self.restore()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("target was not changed", result.stderr)
                self.assertEqual((self.target / "existing").read_text(), "keep me")
                self.assertFalse((self.root / "stage").exists())


if __name__ == "__main__":
    unittest.main()
