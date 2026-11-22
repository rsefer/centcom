import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-hosts.py"
spec = importlib.util.spec_from_file_location("check_hosts", SCRIPT)
hosts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hosts)

HEAD = "a" * 40
OUTPUT = """head|%s
branch|main
dirty|0
symlink|docker-compose.eastcom.yml
confighash|pihole|h1
confighash|beszel-agent|h2
container|pihole|h1|pihole/pihole:latest|running|/pihole
container|beszel-agent|h2|henrygd/beszel-agent:latest|running|/centcom-beszel-agent-1
image|pihole/pihole:latest|sha256:one|pihole/pihole@sha256:aaa,
image|henrygd/beszel-agent:latest|sha256:two|henrygd/beszel-agent@sha256:bbb,
""" % HEAD


def report(**changes):
    parsed = hosts.parse(OUTPUT)
    parsed.update(changes)
    return parsed


class CheckHostsTests(unittest.TestCase):
    def analyze(self, reports, **kwargs):
        return hosts.analyze(reports, **kwargs)

    def test_parse_reads_every_field(self):
        parsed = hosts.parse(OUTPUT)
        self.assertEqual(parsed["head"], HEAD)
        self.assertEqual(parsed["dirty"], 0)
        self.assertEqual(parsed["hashes"], {"pihole": "h1", "beszel-agent": "h2"})
        self.assertEqual(len(parsed["containers"]), 2)
        self.assertEqual(parsed["containers"][0]["name"], "pihole")
        self.assertEqual(hosts.parse("plan|pihole|Recreate\n")["plan"], [("pihole", "Recreate")])
        self.assertEqual(parsed["images"]["pihole/pihole:latest"]["digests"], {"sha256:aaa"})

    def test_digest_equal_to_image_id_is_ignored(self):
        parsed = hosts.parse("image|centcom/x:local|sha256:abc|centcom/x@sha256:abc,\n")
        self.assertEqual(parsed["images"]["centcom/x:local"]["digests"], set())

    def test_matching_hosts_have_no_drift(self):
        reports = {"eastcom": report(), "northcom": report(symlink="docker-compose.northcom.yml")}
        links = {h: f"docker-compose.{h}.yml" for h in reports}
        self.assertEqual(self.analyze(reports, expected_head=HEAD, expected_links=links), [])

    def test_hosts_on_different_commits(self):
        reports = {"eastcom": report(), "northcom": report(head="b" * 40)}
        problems = self.analyze(reports)
        self.assertTrue(any("different commits" in p for p in problems))

    def test_host_behind_published_branch(self):
        problems = self.analyze({"eastcom": report()}, expected_head="c" * 40)
        self.assertTrue(any("origin/main is ccccccc" in p for p in problems))

    def test_dirty_branch_and_symlink(self):
        problems = self.analyze({"eastcom": report(dirty=2, branch="dev")},
                                expected_links={"eastcom": "docker-compose.northcom.yml"})
        text = "\n".join(problems)
        self.assertIn("2 uncommitted", text)
        self.assertIn("branch dev", text)
        self.assertIn("compose.yaml -> docker-compose.eastcom.yml", text)

    def test_stale_config_hash(self):
        parsed = report()
        parsed["hashes"]["pihole"] = "changed"
        problems = self.analyze({"eastcom": parsed})
        self.assertEqual(problems, ["eastcom: pihole was created from an older Compose "
                                    "config (recreate it)"])

    def test_compose_plan_reports_pending_changes(self):
        parsed = report(plan=[("pihole", "Recreate")])
        problems = self.analyze({"eastcom": parsed})
        self.assertEqual(len(problems), 1)
        self.assertIn("would recreate pihole", problems[0])

    def test_plan_does_not_repeat_a_flagged_service(self):
        parsed = report(plan=[("pihole", "Recreate")])
        parsed["hashes"]["pihole"] = "changed"
        problems = self.analyze({"eastcom": parsed})
        self.assertEqual(len(problems), 1)
        self.assertIn("older Compose config", problems[0])

    def test_plan_maps_container_names_to_services(self):
        parsed = report(plan=[("centcom-beszel-agent-1", "Recreate")])
        self.assertIn("would recreate centcom-beszel-agent-1",
                      self.analyze({"eastcom": parsed})[0])

    def test_missing_stopped_and_orphan_containers(self):
        parsed = report()
        parsed["containers"] = [c for c in parsed["containers"] if c["service"] != "pihole"]
        parsed["containers"][0]["state"] = "exited"
        parsed["containers"].append({"service": "old", "hash": "x", "ref": "x",
                                     "state": "running", "name": "old"})
        text = "\n".join(self.analyze({"eastcom": parsed}))
        self.assertIn("pihole is configured but has no container", text)
        self.assertIn("beszel-agent is exited", text)
        self.assertIn("old is running but not in the Compose config", text)

    def test_digest_mismatch_across_hosts(self):
        other = report()
        other["images"]["pihole/pihole:latest"]["digests"] = {"sha256:zzz"}
        text = "\n".join(self.analyze({"eastcom": report(), "northcom": other},
                                      expected_head=HEAD))
        self.assertIn("pihole/pihole:latest resolves to different digests", text)

    def test_locally_built_images_are_not_compared_by_digest(self):
        first, second = report(), report()
        for parsed in (first, second):
            parsed["images"]["pihole/pihole:latest"]["digests"] = set()
        self.assertEqual(self.analyze({"a": first, "b": second}, expected_head=HEAD), [])

    def test_unreachable_host_is_reported(self):
        problems = self.analyze({"eastcom": {"unreachable": "timed out"}})
        self.assertEqual(problems, ["eastcom: unreachable (timed out)"])


if __name__ == "__main__":
    unittest.main()
