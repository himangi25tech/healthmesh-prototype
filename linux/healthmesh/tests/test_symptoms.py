# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.symptoms: journal logging and pattern rules."""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from healthmesh.outbox import Outbox
from healthmesh.symptoms import SymptomJournal

NOW = datetime(2026, 10, 8, 12, 0)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="healthmesh-symptoms-")
        self.journal = SymptomJournal(os.path.join(self.tmp, "journal.jsonl"))
        self.outbox = Outbox(os.path.join(self.tmp, "outbox.jsonl"))
        self.events = []

    def tearDown(self):
        self.outbox.close()

    def check(self, now, ctx):
        return self.journal.check_patterns(now, ctx, self.outbox, self.events.append)


class TestSymptomJournal(_Case):
    def test_severity_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self.journal.log("headache", 0, "none", NOW)
        with self.assertRaises(ValueError):
            self.journal.log("headache", 11, "none", NOW)

    def test_severity_non_int_raises(self):
        with self.assertRaises(ValueError):
            self.journal.log("headache", 5.5, "none", NOW)
        with self.assertRaises(ValueError):
            self.journal.log("headache", True, "none", NOW)

    def test_log_returns_entry_and_round_trips(self):
        entry = self.journal.log("Headache", 7, "behind left eye", NOW)
        self.assertEqual(entry["name"], "headache")  # lowercased
        self.assertEqual(entry["severity"], 7)
        self.assertEqual(entry["note"], "behind left eye")
        self.assertEqual(entry["ts"], NOW.isoformat())
        # JSONL round-trip through a fresh instance.
        again = SymptomJournal(os.path.join(self.tmp, "journal.jsonl"))
        self.assertEqual(again.recent(24, NOW), [entry])

    def test_recent_window(self):
        self.journal.log("fatigue", 8, "", NOW - timedelta(hours=23))
        self.journal.log("fatigue", 8, "", NOW - timedelta(hours=25))
        recent = self.journal.recent(24, NOW)
        self.assertEqual(len(recent), 1)

    def test_tick_and_handle_event_contract(self):
        self.assertEqual(self.journal.tick(NOW, 60), [])
        self.assertIsNone(self.journal.handle_event({"type": "x", "level": "info",
                                                     "detail": {}}))

    def test_headache_rule_fires_with_missed_med(self):
        self.journal.log("headache", 7, "throbbing", NOW - timedelta(hours=2))
        ctx = {"meds": {}, "vitals_avg": None,
               "meds_missed_24h": ["morning BP pill"]}
        emitted = self.check(NOW, ctx)
        self.assertEqual(len(emitted), 1)
        event = emitted[0]
        self.assertEqual(event["type"], "symptom.pattern")
        self.assertEqual(event["level"], "info")
        self.assertEqual(event["detail"]["rule"], "headache_pattern")
        with open(os.path.join(self.tmp, "outbox.jsonl"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("Muse note: Headache (7/10) logged", text)
        self.assertIn("morning BP pill", text)
        self.assertIn("not a diagnosis", text)

    def test_headache_rule_does_not_fire_without_missed_med(self):
        self.journal.log("migraine", 8, "", NOW - timedelta(hours=2))
        ctx = {"meds": {}, "vitals_avg": None, "meds_missed_24h": []}
        self.assertEqual(self.check(NOW, ctx), [])
        self.assertEqual(self.events, [])

    def test_headache_rule_does_not_fire_below_severity_6(self):
        self.journal.log("headache", 5, "", NOW - timedelta(hours=2))
        ctx = {"meds": {}, "vitals_avg": None,
               "meds_missed_24h": ["morning BP pill"]}
        self.assertEqual(self.check(NOW, ctx), [])

    def test_headache_rule_fires_once_per_24h(self):
        self.journal.log("headache", 7, "", NOW - timedelta(hours=2))
        ctx = {"meds": {}, "vitals_avg": None,
               "meds_missed_24h": ["morning BP pill"]}
        self.assertEqual(len(self.check(NOW, ctx)), 1)
        # Second check within 24h: quiet (fresh entry, throttled).
        self.journal.log("headache", 7, "", NOW)
        self.assertEqual(self.check(NOW + timedelta(hours=1), ctx), [])
        # After 24h: fires again (fresh entry, throttle window expired).
        self.journal.log("headache", 7, "", NOW + timedelta(hours=24))
        self.assertEqual(
            len(self.check(NOW + timedelta(hours=24, minutes=1), ctx)), 1)

    def test_fatigue_rule_fires_with_low_sleep(self):
        self.journal.log("fatigue", 6, "drained", NOW - timedelta(hours=3))
        ctx = {"meds": {}, "vitals_avg": {"hrv_ms": 42.0, "sleep_hours": 4.5},
               "meds_missed_24h": []}
        emitted = self.check(NOW, ctx)
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["detail"]["rule"], "fatigue_pattern")
        with open(os.path.join(self.tmp, "outbox.jsonl"), encoding="utf-8") as fh:
            self.assertIn("Muse note: fatigue 6/10 with 4.5h sleep", fh.read())

    def test_fatigue_rule_does_not_fire_with_enough_sleep(self):
        self.journal.log("fatigue", 8, "", NOW - timedelta(hours=3))
        ctx = {"meds": {}, "vitals_avg": {"hrv_ms": 42.0, "sleep_hours": 7.5},
               "meds_missed_24h": []}
        self.assertEqual(self.check(NOW, ctx), [])

    def test_fatigue_rule_fires_once_per_24h(self):
        self.journal.log("fatigue", 7, "", NOW - timedelta(hours=3))
        ctx = {"meds": {}, "vitals_avg": {"sleep_hours": 5.0},
               "meds_missed_24h": []}
        self.assertEqual(len(self.check(NOW, ctx)), 1)
        self.assertEqual(self.check(NOW + timedelta(hours=2), ctx), [])


if __name__ == "__main__":
    unittest.main()
