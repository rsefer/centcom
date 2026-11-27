import subprocess
import unittest

from remote_fixture import COMMIT_MESSAGE, RemoteHostsFixture


class RemoteDeployTests(RemoteHostsFixture):
    def previous(self, host):
        return subprocess.check_output(
            ["git", "rev-parse", "refs/centcom/previous-deploy"],
            cwd=self.homes / host / "centcom", text=True).strip()

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

    def test_records_previous_commit_only_when_head_moves(self):
        first = self.head("northcom")
        second = self.publish("second")
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.previous("northcom"), first)
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.previous("northcom"), first)
        self.assertEqual(self.head("northcom"), second)
        third = self.publish("third")
        self.assertEqual(self.deploy().returncode, 0)
        self.assertEqual(self.previous("southcom"), second)
        self.assertEqual(self.head("southcom"), third)

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
