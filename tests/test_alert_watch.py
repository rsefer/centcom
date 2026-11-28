import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "alert-watch.py"
spec = importlib.util.spec_from_file_location("alert_watch", SCRIPT)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

NOW = datetime.datetime(2026, 11, 27, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
HOUR = 3600


def stamp(hours_ago):
    moment = datetime.datetime.fromtimestamp(NOW - hours_ago * HOUR, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Track(unittest.TestCase):
    def test_start_needs_confirmation_then_is_quiet_until_a_reminder(self):
        state = {}
        self.assertIsNone(watch.track(state, "k", True, NOW, confirm=3, remind=HOUR))
        self.assertIsNone(watch.track(state, "k", True, NOW, confirm=3, remind=HOUR))
        self.assertEqual(watch.track(state, "k", True, NOW, confirm=3, remind=HOUR), "start")
        watch.acknowledge(state, "k", "start", NOW)
        self.assertIsNone(watch.track(state, "k", True, NOW + 60, confirm=3, remind=HOUR))
        self.assertEqual(watch.track(state, "k", True, NOW + HOUR, confirm=3, remind=HOUR), "remind")

    def test_a_blip_resets_the_streak(self):
        state = {}
        for active in (True, True, False, True, True):
            self.assertIsNone(watch.track(state, "k", active, NOW, confirm=3))
        self.assertEqual(watch.track(state, "k", True, NOW, confirm=3), "start")

    def test_unsent_start_is_retried_until_acknowledged(self):
        state = {}
        self.assertEqual(watch.track(state, "k", True, NOW), "start")
        self.assertEqual(watch.track(state, "k", True, NOW + 300), "start")
        watch.acknowledge(state, "k", "start", NOW + 300)
        self.assertIsNone(watch.track(state, "k", True, NOW + 600))

    def test_end_is_sent_once_and_only_after_a_start_was_sent(self):
        state = {}
        self.assertIsNone(watch.track(state, "quiet", False, NOW))
        watch.track(state, "k", True, NOW)
        self.assertIsNone(watch.track(state, "k", False, NOW))  # start never sent
        watch.track(state, "k", True, NOW)
        watch.acknowledge(state, "k", "start", NOW)
        self.assertEqual(watch.track(state, "k", False, NOW), "end")
        self.assertEqual(watch.track(state, "k", False, NOW), "end")  # retried
        watch.acknowledge(state, "k", "end", NOW)
        self.assertIsNone(watch.track(state, "k", False, NOW))


class LocalChecks(unittest.TestCase):
    def test_backup_age_threshold_matches_the_dashboard(self):
        self.assertFalse(watch.backup_overdue(stamp(47), NOW, NOW, "h")[0])
        overdue, detail = watch.backup_overdue(stamp(49), NOW, NOW, "northcom")
        self.assertTrue(overdue)
        self.assertIn("northcom", detail)
        self.assertIn("49 hours ago", detail)

    def test_missing_or_invalid_marker_waits_for_the_watcher_to_age(self):
        for marker in (None, "garbage"):
            self.assertFalse(watch.backup_overdue(marker, NOW, NOW - 47 * HOUR, "h")[0])
            self.assertTrue(watch.backup_overdue(marker, NOW, NOW - 49 * HOUR, "h")[0])

    def test_only_the_latest_selfheal_event_counts(self):
        suppressed = "2026-11-27 11:00:00 reboot suppressed: 3 consecutive attempts"
        active, detail = watch.selfheal_attention(["a", "b", suppressed], "southcom")
        self.assertTrue(active)
        self.assertIn(suppressed, detail)
        self.assertEqual(watch.selfheal_attention([suppressed, "later event"], "h"), (False, ""))
        self.assertEqual(watch.selfheal_attention([], "h"), (False, ""))

    def test_local_rows_read_the_marker_and_event_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".last-success").write_text(stamp(60) + "\n")
            events = root / "events.log"
            events.write_text("2026-11-27 11:00:00 reboot suppressed: needs manual attention\n")
            rows = {r["key"]: r for r in watch.local_rows(
                {"BACKUP_ROOT": temp}, {}, NOW, "northcom", selfheal_path=events)}
            self.assertTrue(rows["backup"]["active"])
            self.assertTrue(rows["selfheal"]["active"])
            rows = {r["key"] for r in watch.local_rows(
                {"BACKUP_ROOT": temp}, {}, NOW, "northcom", selfheal_path=root / "absent")}
            self.assertEqual(rows, {"backup"})


class Probes(unittest.TestCase):
    CONFIG = {"hosts": ["northcom", "southcom", "eastcom"], "status": {"port": 18086},
              "proxy": {"primary": "northcom", "standby": "southcom"},
              "services": {"pihole": {"placement": [{"host": "northcom"}, {"host": "eastcom"}]}}}

    def test_probe_host_is_the_first_without_a_pihole_unless_pinned(self):
        self.assertEqual(watch.probe_host_name(self.CONFIG, {}), "southcom")
        self.assertEqual(watch.probe_host_name(self.CONFIG, {"DNS_FAILOVER_HOST": "eastcom"}), "eastcom")

    def test_probe_rows_report_each_host_and_application(self):
        config = dict(self.CONFIG, services={
            "pihole": self.CONFIG["services"]["pihole"],
            "rss": {"dashboard": "primary", "title": "RSS", "icon": "i"},
            "ha": {"dashboard": "primary", "path": "/x", "title": "HA", "icon": "i"},
            "hidden": {}})
        values = {"VPN_DOMAIN": "example.test", "TAILSCALE_IP_NORTHCOM": "100.1.1.1",
                  "TAILSCALE_IP_SOUTHCOM": "100.1.1.2"}
        with mock.patch.object(watch, "probe_agent",
                               side_effect=lambda ip, port: "refused" if ip == "100.1.1.2" else None), \
             mock.patch.object(watch, "probe_app",
                               side_effect=lambda url: "HTTP 502" if "ha." in url else None):
            rows = {r["key"]: r for r in watch.probe_checks(config, values, "southcom")}
        self.assertEqual(set(rows), {"host:northcom", "host:southcom", "app:rss", "app:ha"})
        self.assertTrue(rows["host:southcom"]["active"])
        self.assertIn("100.1.1.2:18086", rows["host:southcom"]["detail"])
        self.assertTrue(rows["app:ha"]["active"])
        self.assertIn("https://ha.example.test/x", rows["app:ha"]["detail"])
        self.assertFalse(rows["app:rss"]["active"])
        self.assertEqual(rows["app:rss"]["confirm"], 3)


class Compose(unittest.TestCase):
    def test_subject_lists_problems_and_recoveries(self):
        due = [("a", "start", "backup overdue", "Backups are overdue."),
               ("b", "end", "rss down", "rss is reachable again.")]
        subject, body = watch.compose("southcom", due)
        self.assertEqual(subject, "[centcom] southcom: backup overdue; recovered: rss down")
        self.assertIn("* Backups are overdue.", body)
        self.assertIn("* rss is reachable again.", body)


class Main(unittest.TestCase):
    def run_main(self, temp, argv=(), configured=True, sent=True):
        env = {"BACKUP_ROOT": temp, "DNS_ALERT_FROM": "a@x", "DNS_ALERT_TO": "b@x",
               "SMTP_HOST": "smtp.test"} if configured else {"BACKUP_ROOT": temp}
        patches = [mock.patch.dict(os.environ, {"CENTCOM_ALERT_STATE": str(Path(temp) / "state")}),
                   mock.patch.object(watch.alerts, "settings", return_value=env),
                   mock.patch.object(watch.socket, "gethostname", return_value="northcom.local"),
                   mock.patch.object(watch, "SELFHEAL_EVENTS", Path(temp) / "absent"),
                   mock.patch.object(watch.registry, "load", side_effect=OSError("no services.toml")),
                   mock.patch.object(watch, "log"),
                   mock.patch.object(watch.alerts, "send_email", return_value=sent)]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6] as send:
            with mock.patch.object(watch.datetime, "datetime", wraps=datetime.datetime) as clock:
                clock.now.return_value = datetime.datetime.fromtimestamp(NOW, datetime.timezone.utc)
                self.assertEqual(watch.main(list(argv)), 0)
        return send

    def test_overdue_backup_emails_once_and_failed_sends_are_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".last-success").write_text(stamp(72) + "\n")
            self.assertEqual(self.run_main(temp, sent=False).call_count, 1)
            self.assertEqual(self.run_main(temp, sent=True).call_count, 1)  # retried
            send = self.run_main(temp)
            send.assert_not_called()  # acknowledged
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".last-success").write_text(stamp(72) + "\n")
            send = self.run_main(temp)
            subject = send.call_args.args[1]
            self.assertEqual(subject, "[centcom] northcom: backup overdue")

    def test_unconfigured_smtp_does_not_nag(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".last-success").write_text(stamp(72) + "\n")
            self.assertEqual(self.run_main(temp, configured=False, sent=False).call_count, 1)
            self.run_main(temp, configured=False, sent=False).assert_not_called()

    def test_dry_run_sends_nothing_and_saves_no_state(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / ".last-success").write_text(stamp(72) + "\n")
            self.run_main(temp, ["--dry-run"]).assert_not_called()
            self.assertFalse((Path(temp) / "state").exists())

    def test_recovery_emails_after_the_backup_is_current(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / ".last-success"
            marker.write_text(stamp(72) + "\n")
            self.run_main(temp)
            marker.write_text(stamp(1) + "\n")
            send = self.run_main(temp)
            self.assertEqual(send.call_args.args[1], "[centcom] northcom: recovered: backup overdue")
            state = json.loads((Path(temp) / "state" / "state.json").read_text())
            self.assertIsNone(state["checks"]["backup"]["alerted"])


if __name__ == "__main__":
    unittest.main()
