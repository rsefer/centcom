import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CategoryDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copy(ROOT / "scripts/freshrss-category-defaults.sh",
                    self.root / "scripts/freshrss-category-defaults.sh")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_LOG"\ncat > "$TEST_STDIN"\n')
        docker.chmod(0o755)
        self.log = self.root / "docker.log"
        self.stdin = self.root / "stdin.php"

    def run_script(self, **extra):
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                   TEST_LOG=str(self.log), TEST_STDIN=str(self.stdin))
        env.pop("CATEGORY_DEDUP_N", None)
        env.pop("DRY_RUN", None)
        env.update(extra)
        return subprocess.run(["sh", str(self.root / "scripts/freshrss-category-defaults.sh")],
                              env=env, capture_output=True, text=True)

    def test_runs_php_in_the_rss_container_as_www_data_with_defaults(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        call = self.log.read_text().strip()
        self.assertIn("compose exec -T -u www-data", call)
        self.assertIn("-e CATEGORY_DEDUP_N=100", call)
        self.assertTrue(call.endswith("rss php"), call)

    def test_env_overrides_are_passed_through(self):
        self.run_script(CATEGORY_DEDUP_N="250", DRY_RUN="1")
        call = self.log.read_text()
        self.assertIn("-e CATEGORY_DEDUP_N=250", call)
        self.assertIn("-e DRY_RUN=1", call)

    def test_php_covers_every_category_and_keeps_existing_values(self):
        self.run_script()
        php = self.stdin.read_text()
        self.assertIn("listCategories(false)", php)
        self.assertIn("read_when_same_title_in_category", php)
        self.assertIn("read_when_same_guid_in_category", php)
        # Only fills in what is missing.
        self.assertIn("!$category->hasAttribute($k)", php)
        self.assertIn("updateCategory(", php)


if __name__ == "__main__":
    unittest.main()
