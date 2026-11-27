import subprocess
import unittest

from remote_fixture import RemoteHostsFixture


class RemoteRollbackTests(RemoteHostsFixture):
    def run_script(self, script, *args, **env):
        return subprocess.run(["sh", f"scripts/{script}", *args], cwd=self.work,
                              env=dict(self.env, **env), capture_output=True, text=True)

    def deploy(self, *args):
        result = self.run_script("remote-deploy.sh", *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.log.unlink()

    def rollback(self, *args, **env):
        return self.run_script("remote-rollback.sh", *args, **env)

    def test_rolls_every_host_back_to_its_previous_deploy_without_pulling(self):
        first = self.head("northcom")
        self.publish("second")
        self.deploy()
        result = self.rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head("northcom"), first)
        self.assertEqual(self.head("southcom"), first)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
            "connect northcom", "northcom setup-cron.sh", "northcom reload.sh skip-pull",
            "connect southcom", "southcom setup-cron.sh", "southcom reload.sh skip-pull",
            "connect southcom", "southcom check-services.py",
        ])

    def test_later_deploy_fast_forwards_after_a_rollback(self):
        self.publish("second")
        self.deploy()
        self.assertEqual(self.rollback().returncode, 0)
        latest = self.publish("third")
        self.deploy()
        self.assertEqual(self.head("northcom"), latest)

    def test_explicit_commit_overrides_the_recorded_one(self):
        first = self.head("northcom")
        self.publish("second")
        self.deploy()
        self.publish("third")
        self.deploy()
        result = self.rollback("all", "main", first[:12])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head("southcom"), first)

    def test_single_host_rollback_leaves_others_alone(self):
        first = self.head("northcom")
        second = self.publish("second")
        self.deploy()
        result = self.rollback("southcom.local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head("southcom"), first)
        self.assertEqual(self.head("northcom"), second)

    def test_second_rollback_does_not_flip_back(self):
        self.publish("second")
        self.deploy()
        self.assertEqual(self.rollback().returncode, 0)
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already at", result.stderr)

    def test_no_recorded_deploy_requires_a_commit(self):
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no previous deploy is recorded", result.stderr)

    def test_hosts_with_different_previous_commits_require_a_commit(self):
        first = self.head("northcom")
        self.publish("second")
        self.deploy("southcom.local")
        self.publish("third")
        self.deploy()
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different commits", result.stderr)
        self.assertNotEqual(self.head("northcom"), first)
        self.assertNotIn("reload.sh", self.log.read_text())

    def test_commit_must_be_an_ancestor_of_the_host(self):
        first = self.head("northcom")
        self.publish("second")
        self.deploy()
        self.git("checkout", "-q", "-b", "side", first)
        self.git("commit", "-q", "--allow-empty", "-m", "side")
        side = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                       cwd=self.work, text=True).strip()
        self.git("checkout", "-q", "main")
        self.git("push", "-q", "origin", "side")
        for host in ("northcom", "southcom"):
            subprocess.run(["git", "fetch", "-q", "origin", "side"],
                           cwd=self.homes / host / "centcom", check=True)
        result = self.rollback("all", "main", side)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an ancestor", result.stderr)

    def test_dirty_host_prevents_all_rollbacks(self):
        self.publish("second")
        self.deploy()
        (self.homes / "southcom" / "centcom" / "unexpected.txt").write_text("local")
        result = self.rollback()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
        ])

    def test_unreachable_host_prevents_all_rollbacks(self):
        self.publish("second")
        self.deploy()
        result = self.rollback(FAIL_HOST="southcom")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.log.read_text().splitlines(), [
            "connect northcom", "connect southcom",
        ])

    def test_failed_service_check_fails_rollback(self):
        self.publish("second")
        self.deploy()
        result = self.rollback(CHECK_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("southcom check-services.py", self.log.read_text())

    def test_rejects_malformed_commit(self):
        for bad in ("HEAD~1", "abc", "main;ls"):
            result = self.rollback("all", "main", bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn("Invalid commit", result.stderr)


if __name__ == "__main__":
    unittest.main()
