import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dns-failover.py"
spec = importlib.util.spec_from_file_location("dns_failover", SCRIPT)
failover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(failover)

N, E = "100.1.1.1", "100.2.2.2"


def config(addresses, override=True):
    return {"nameservers": [{"address": a, "useWithExitNode": True} for a in addresses],
            "preferences": {"overrideLocalDNS": override, "magicDNS": True}}


class Observe(unittest.TestCase):
    def test_down_needs_consecutive_failures_and_up_needs_fewer(self):
        state = {}
        self.assertIsNone(failover.observe(state, "n", True, 3, 2))
        self.assertIsNone(failover.observe(state, "n", False, 3, 2))
        self.assertIsNone(failover.observe(state, "n", False, 3, 2))
        self.assertIsNone(failover.observe(state, "n", True, 3, 2))  # blip resets
        self.assertEqual(state["n"]["streak"], 0)
        for _ in range(2):
            self.assertIsNone(failover.observe(state, "n", False, 3, 2))
        self.assertEqual(failover.observe(state, "n", False, 3, 2), "down")
        self.assertIsNone(failover.observe(state, "n", True, 3, 2))
        self.assertEqual(failover.observe(state, "n", True, 3, 2), "up")


class Plan(unittest.TestCase):
    def test_steady_state_changes_nothing(self):
        self.assertEqual(failover.plan(config([N, E]), [N, E], {N: True, E: True}), (None, []))

    def test_removes_down_host_and_keeps_override(self):
        new, changes = failover.plan(config([N, E]), [N, E], {N: True, E: False})
        self.assertEqual([ns["address"] for ns in new["nameservers"]], [N])
        self.assertTrue(new["preferences"]["overrideLocalDNS"])
        self.assertEqual(len(changes), 1)

    def test_readds_in_placement_order(self):
        new, _ = failover.plan(config([E]), [N, E], {N: True, E: True})
        self.assertEqual([ns["address"] for ns in new["nameservers"]], [N, E])

    def test_all_down_disables_override_and_keeps_list(self):
        new, _ = failover.plan(config([N]), [N, E], {N: False, E: False})
        self.assertEqual([ns["address"] for ns in new["nameservers"]], [N])
        self.assertFalse(new["preferences"]["overrideLocalDNS"])

    def test_recovery_enables_override_again(self):
        new, _ = failover.plan(config([N], override=False), [N, E], {N: False, E: True})
        self.assertEqual([ns["address"] for ns in new["nameservers"]], [E])
        self.assertTrue(new["preferences"]["overrideLocalDNS"])

    def test_unmanaged_nameservers_are_kept_and_other_fields_preserved(self):
        dns = config(["9.9.9.9", N, E])
        dns["splitDNS"] = {"x.test": ["1.1.1.1"]}
        new, _ = failover.plan(dns, [N, E], {N: False, E: True})
        self.assertEqual([ns["address"] for ns in new["nameservers"]], [E, "9.9.9.9"])
        self.assertEqual(new["splitDNS"], dns["splitDNS"])


if __name__ == "__main__":
    unittest.main()
