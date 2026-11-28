import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1] / "scripts"


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


proxy = load("proxy_failover", "proxy-failover.py")
observe = load("dns_failover", "dns-failover.py").observe

DOMAIN = "vpn.example.test"
NORTH, SOUTH = "100.1.1.1", "100.1.1.2"
ENV = {"VPN_DOMAIN": DOMAIN, "TAILSCALE_IP_NORTHCOM": NORTH, "TAILSCALE_IP_SOUTHCOM": SOUTH,
       "PROXY_FAILOVER": "on"}


def record(name, address, **extra):
    base = {"raw_name": name.replace("*", "\\052") + ".", "name": name, "type": "A", "ttl": 300,
            "values": [address], "alias": False, "set_id": None}
    return dict(base, **extra)


class FakeZone:
    def __init__(self, address=NORTH, extra=()):
        self.records = [record(DOMAIN, address), record(f"*.{DOMAIN}", address), *extra]
        self.upserts = []

    def list_records(self):
        return [dict(r) for r in self.records]

    def upsert(self, comment, records, address):
        self.upserts.append((comment, [r["name"] for r in records], address))
        names = {r["name"] for r in records}
        for r in self.records:
            if r["name"] in names:
                r["values"] = [address]


class Runner:
    """Runs reconcile repeatedly with scripted proxy health."""

    def __init__(self, zone=None, env=None):
        self.zone, self.state, self.health = zone or FakeZone(), {}, {NORTH: True, SOUTH: True}
        self.env = dict(ENV, **(env or {}))

    def run(self, times=1, **kwargs):
        result = None
        for _ in range(times):
            result = proxy.reconcile(self.env, self.state, observe=observe, client=self.zone,
                                     probe=lambda ip, domain: self.health[ip], log=lambda m: None,
                                     **kwargs)
        return result


class Plan(unittest.TestCase):
    ADDRESSES = {"primary": NORTH, "standby": SOUTH}

    def test_changes_only_records_not_already_at_the_target(self):
        records = [record(DOMAIN, SOUTH), record(f"*.{DOMAIN}", NORTH)]
        changes, problems = proxy.plan(records, DOMAIN, self.ADDRESSES, "standby")
        self.assertEqual([r["name"] for r in changes], [f"*.{DOMAIN}"])
        self.assertEqual(problems, [])

    def test_any_problem_means_no_changes_at_all(self):
        for odd in (record(f"*.{DOMAIN}", "9.9.9.9"),
                    record(f"*.{DOMAIN}", NORTH, alias=True),
                    record(f"*.{DOMAIN}", NORTH, set_id="x"),
                    record(f"*.{DOMAIN}", NORTH, values=[NORTH, SOUTH])):
            changes, problems = proxy.plan([record(DOMAIN, NORTH), odd], DOMAIN, self.ADDRESSES, "standby")
            self.assertEqual((changes, len(problems)), ([], 1), odd)
        changes, problems = proxy.plan([record(DOMAIN, NORTH)], DOMAIN, self.ADDRESSES, "standby")
        self.assertEqual((changes, problems), ([], [f"no A record named *.{DOMAIN} in the hosted zone"]))

    def test_unmanaged_records_at_a_proxy_are_listed(self):
        records = [record(DOMAIN, NORTH), record(f"gym.{DOMAIN}", NORTH),
                   record(f"other.{DOMAIN}", "9.9.9.9"), record("elsewhere.test", NORTH)]
        self.assertEqual(proxy.unmanaged_at_proxy(records, DOMAIN, self.ADDRESSES), [f"gym.{DOMAIN}"])


class Reconcile(unittest.TestCase):
    def test_disabled_by_default(self):
        self.assertFalse(proxy.enabled({}))
        self.assertFalse(proxy.enabled({"PROXY_FAILOVER": "off"}))
        self.assertTrue(proxy.enabled({"PROXY_FAILOVER": "On"}))

    def test_steady_state_changes_nothing(self):
        runner = Runner()
        self.assertEqual(runner.run(5), ([], None))
        self.assertEqual(runner.zone.upserts, [])

    def test_fails_over_after_three_failed_runs_and_reports_it(self):
        runner = Runner()
        runner.run(2)  # lets the standby prove itself
        runner.health[NORTH] = False
        self.assertEqual(runner.run(2)[1], None)
        messages, changed = runner.run()
        self.assertEqual(changed, "standby")
        self.assertEqual(runner.zone.upserts, [(
            "centcom proxy failover to southcom", [DOMAIN, f"*.{DOMAIN}"], SOUTH)])
        self.assertTrue(any("DOWN" in m for m in messages))
        self.assertTrue(any("DNS now points at the standby proxy" in m for m in messages))
        self.assertEqual(runner.run(5), ([], None))  # stays put, one change only
        self.assertEqual(len(runner.zone.upserts), 1)

    def test_never_switches_to_an_unverified_or_dead_standby(self):
        runner = Runner()
        runner.health.update({NORTH: False, SOUTH: False})
        self.assertEqual(runner.run(6)[1], None)
        runner = Runner()
        runner.health.update({NORTH: False, SOUTH: False})
        runner.run(2)
        runner.health[SOUTH] = True  # the primary is now confirmed down, the standby is not yet up
        self.assertEqual(runner.run(1)[1], None)
        self.assertEqual(runner.zone.upserts, [])
        self.assertEqual(runner.run(1)[1], "standby")

    def test_a_single_failed_first_probe_does_not_move_dns(self):
        runner = Runner()
        runner.health[NORTH] = False
        runner.run(1)
        runner.health[NORTH] = True
        runner.run(5)
        self.assertEqual(runner.zone.upserts, [])

    def test_fails_back_only_after_the_primary_is_healthy_for_ten_runs(self):
        runner = Runner()
        runner.run(2)
        runner.health[NORTH] = False
        runner.run(3)
        runner.health[NORTH] = True
        self.assertEqual(runner.run(9)[1], None)
        runner.health[NORTH] = False
        runner.run(1)
        runner.health[NORTH] = True
        self.assertEqual(runner.run(9)[1], None)  # the blip restarted the count
        messages, changed = runner.run()
        self.assertEqual(changed, "primary")
        self.assertEqual(runner.zone.upserts[-1][2], NORTH)
        self.assertTrue(any("healthy again" in m for m in messages))

    def test_paused_and_dry_run_change_nothing(self):
        for flag in ({"paused": True}, {"dry_run": True}):
            runner = Runner()
            runner.run(2)
            runner.health[NORTH] = False
            self.assertEqual(runner.run(3, **flag)[1], None)
            self.assertEqual(runner.zone.upserts, [])

    def test_manual_edit_back_to_the_standby_is_corrected_while_the_primary_is_healthy(self):
        runner = Runner(FakeZone(address=SOUTH))
        self.assertEqual(runner.run(1)[1], "primary")

    def test_a_record_problem_is_reported_once_and_changes_nothing(self):
        zone = FakeZone()
        zone.records[1]["values"] = ["9.9.9.9"]
        runner = Runner(zone)
        first = runner.run()
        self.assertTrue(any("cannot change DNS" in m and "not a proxy address" in m for m in first[0]))
        runner.health[NORTH] = False
        later = [m for _ in range(6) for m in runner.run()[0]]
        self.assertFalse(any("cannot change DNS" in m for m in later))  # reported once only
        self.assertEqual(zone.upserts, [])

    def test_unmanaged_proxy_records_are_warned_about_once(self):
        runner = Runner(FakeZone(extra=[record(f"gym.{DOMAIN}", NORTH)]))
        first = runner.run()
        self.assertTrue(any(f"gym.{DOMAIN}" in m and "will not fail over" in m for m in first[0]))
        self.assertEqual(runner.run(), ([], None))

    def test_incomplete_settings_raise(self):
        runner = Runner(env={"VPN_DOMAIN": ""})
        with self.assertRaisesRegex(RuntimeError, "needs VPN_DOMAIN"):
            runner.run()

    def test_route53_errors_propagate_for_the_caller_to_count(self):
        class Broken(FakeZone):
            def list_records(self):
                raise RuntimeError("Route 53 GET failed: HTTP 403")
        with self.assertRaises(RuntimeError):
            Runner(Broken()).run()

    def test_probe_fails_when_nothing_is_listening(self):
        self.assertFalse(proxy.probe_proxy("127.0.0.1", "example.test", timeout=1))


if __name__ == "__main__":
    unittest.main()
