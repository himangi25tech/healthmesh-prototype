# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.rules: thresholds, ordering, emergency ladder."""

import os
import tempfile
import unittest
from datetime import datetime

from healthmesh.outbox import Outbox
from healthmesh.rules import RulesEngine, LEVELS, level_rank, THRESHOLDS

NOW = datetime(2026, 10, 8, 3, 12)

NORMAL = {"hr": 72, "hrv_ms": 48, "spo2": 98, "bp_sys": 120, "bp_dia": 80}


def vitals(**kw):
    v = dict(NORMAL)
    v.update(kw)
    return v


class EngineHarness:
    """RulesEngine wired to an in-memory event list and a temp outbox."""

    def __init__(self, **kw):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "outbox.jsonl")
        self.outbox = Outbox(self.path,
                             clock=lambda: datetime(2026, 10, 8, 3, 12))
        self.events = []
        self.engine = RulesEngine(self.outbox, self._emit, **kw)

    def _emit(self, etype, level, detail, now):
        self.events.append({"type": etype, "level": level, "detail": detail})

    def outbox_kinds(self):
        import json
        with open(self.path, encoding="utf-8") as fh:
            return [(json.loads(l)["kind"], json.loads(l)["text"])
                    for l in fh if l.strip()]

    def close(self):
        self.outbox.close()


class TestLevels(unittest.TestCase):
    def test_strict_ordering(self):
        self.assertEqual(list(LEVELS), ["info", "nudge", "alert", "emergency"])
        self.assertLess(level_rank("info"), level_rank("nudge"))
        self.assertLess(level_rank("nudge"), level_rank("alert"))
        self.assertLess(level_rank("alert"), level_rank("emergency"))


class TestThresholds(unittest.TestCase):
    def setUp(self):
        self.h = EngineHarness()

    def tearDown(self):
        self.h.close()

    def feed(self, v, minutes, start=0):
        for m in range(start, start + minutes):
            self.h.engine.evaluate(m, v, NOW)
            self.h.engine.check_timeouts(m, NOW)

    def test_hr_alert_after_5_sustained_minutes(self):
        v = vitals(hr=125)
        self.feed(v, 4)
        self.assertIsNone(self.h.engine.active_alert)
        self.feed(v, 1, start=4)
        alert = self.h.engine.active_alert
        self.assertIsNotNone(alert)
        self.assertEqual(alert["level"], "alert")
        self.assertEqual(self.h.events[-1]["type"], "vitals.anomaly")
        self.assertEqual(self.h.events[-1]["level"], "alert")
        self.assertEqual(self.h.outbox_kinds()[-1][0], "alert")

    def test_hr_below_threshold_does_not_fire(self):
        self.feed(vitals(hr=119), 10)
        self.assertIsNone(self.h.engine.active_alert)

    def test_hr_emergency_after_3_sustained_minutes(self):
        v = vitals(hr=145)
        self.feed(v, 2)
        self.assertIsNone(self.h.engine.active_alert)
        self.feed(v, 1, start=2)
        alert = self.h.engine.active_alert
        self.assertIsNotNone(alert)
        self.assertEqual(alert["level"], "emergency")
        types = [e["type"] for e in self.h.events]
        self.assertIn("emergency.confirm_request", types)

    def test_spo2_alert_after_3_sustained_minutes(self):
        v = vitals(spo2=91)
        self.feed(v, 2)
        self.assertIsNone(self.h.engine.active_alert)
        self.feed(v, 1, start=2)
        self.assertEqual(self.h.engine.active_alert["level"], "alert")

    def test_bp_high_emergency_immediate(self):
        self.feed(vitals(bp_sys=185, bp_dia=110), 1)
        self.assertEqual(self.h.engine.active_alert["level"], "emergency")

    def test_bp_low_emergency_immediate(self):
        self.feed(vitals(bp_sys=88, bp_dia=55), 1)
        self.assertEqual(self.h.engine.active_alert["level"], "emergency")

    def test_never_downgrades_while_emergency_holds(self):
        self.feed(vitals(hr=145), 3)  # emergency at minute 2, deadline at 7
        # Alert-level vitals before the deadline: the ladder keeps running and
        # the emergency alert is never downgraded to a plain alert.
        self.feed(vitals(hr=125), 4, start=3)  # minutes 3..6, deadline not hit
        self.assertEqual(self.h.engine.active_alert["level"], "emergency")

    def test_alert_clears_when_condition_recovers(self):
        self.feed(vitals(hr=125), 5)
        self.assertEqual(self.h.engine.active_alert["level"], "alert")
        self.feed(vitals(hr=72), 3, start=5)
        self.assertIsNone(self.h.engine.active_alert)
        self.assertEqual(self.h.events[-1]["level"], "info")
        self.assertEqual(self.h.events[-1]["detail"]["status"], "recovered")


class TestEmergencyLadder(unittest.TestCase):
    def setUp(self):
        self.h = EngineHarness()

    def tearDown(self):
        self.h.close()

    def trigger(self, minute=0):
        e = self.h.engine
        for m in range(minute, minute + 3):
            e.evaluate(m, vitals(hr=150), NOW)
        self.assertEqual(e.active_alert["level"], "emergency")
        return e

    def test_ladder_no_response_contacts_emergency(self):
        e = self.trigger()
        kinds_before = self.h.outbox_kinds()
        self.assertTrue(any("Are you okay?" in t for _, t in kinds_before))
        # 5 sim-minutes pass with no response.
        for m in range(3, 3 + THRESHOLDS["confirm_wait_min"] + 1):
            e.check_timeouts(m, NOW)
        types = [ev["type"] for ev in self.h.events]
        self.assertLess(types.index("emergency.confirm_request"),
                        types.index("emergency.no_response"))
        self.assertLess(types.index("emergency.no_response"),
                        types.index("emergency.contacted"))
        kind, text = self.h.outbox_kinds()[-1]
        self.assertEqual(kind, "emergency")
        self.assertTrue(text.startswith("WhatsApp to Aarav (brother):"),
                        text)
        self.assertIn("Last known location: home.", text)
        self.assertIn("may need help", text)
        self.assertIsNone(e.active_alert)

    def test_ladder_cancels_cleanly_on_user_response(self):
        e = self.trigger()
        e.check_timeouts(3, NOW)
        e.check_timeouts(4, NOW)
        cancelled = e.user_responded(4, "I'm fine, bad dream", NOW)
        self.assertTrue(cancelled)
        for m in range(5, 12):
            e.check_timeouts(m, NOW)
        types = [ev["type"] for ev in self.h.events]
        self.assertIn("user.response", types)
        self.assertNotIn("emergency.contacted", types)
        self.assertNotIn("emergency.no_response", types)
        kind, text = self.h.outbox_kinds()[-1]
        self.assertEqual(kind, "info")
        self.assertIn("cancelled", text)
        self.assertIsNone(e.active_alert)

    def test_response_without_ladder_is_noop(self):
        e = self.h.engine
        self.assertFalse(e.user_responded(0, "hello", NOW))
        self.assertIn("user.response",
                      [ev["type"] for ev in self.h.events])

    def test_ladder_does_not_refire_while_condition_persists(self):
        e = self.trigger()
        for m in range(3, 30):
            e.evaluate(m, vitals(hr=150), NOW)
            e.check_timeouts(m, NOW)
        confirms = [ev for ev in self.h.events
                    if ev["type"] == "emergency.confirm_request"]
        contacted = [ev for ev in self.h.events
                     if ev["type"] == "emergency.contacted"]
        self.assertEqual(len(confirms), 1)
        self.assertEqual(len(contacted), 1)

    def test_latch_releases_when_condition_clears(self):
        e = self.trigger()
        for m in range(3, 10):
            e.evaluate(m, vitals(hr=150), NOW)
            e.check_timeouts(m, NOW)
        # Condition clears, then a new episode begins: ladder may fire again.
        for m in range(10, 13):
            e.evaluate(m, vitals(hr=72), NOW)
            e.check_timeouts(m, NOW)
        for m in range(13, 16):
            e.evaluate(m, vitals(hr=150), NOW)
            e.check_timeouts(m, NOW)
        confirms = [ev for ev in self.h.events
                    if ev["type"] == "emergency.confirm_request"]
        self.assertEqual(len(confirms), 2)


class TestMedEscalation(unittest.TestCase):
    def setUp(self):
        self.h = EngineHarness()

    def tearDown(self):
        self.h.close()

    def test_missed_dose_nudge_then_alert(self):
        e = self.h.engine
        missed = {"type": "med.missed", "level": "nudge",
                  "detail": {"med": "morning_bp_pill",
                             "label": "morning BP pill",
                             "reason": "grace window ended without dispense"}}
        e.on_med_event(missed, 120, NOW)
        kind, _ = self.h.outbox_kinds()[-1]
        self.assertEqual(kind, "reminder")
        # 29 minutes later: still quiet.
        e.check_timeouts(120 + 29, NOW)
        self.assertNotIn("alert", [ev["level"] for ev in self.h.events])
        # 30 minutes later: escalates to alert.
        e.check_timeouts(120 + 30, NOW)
        self.assertEqual(self.h.events[-1]["type"], "med.missed")
        self.assertEqual(self.h.events[-1]["level"], "alert")
        kind, text = self.h.outbox_kinds()[-1]
        self.assertEqual(kind, "alert")
        self.assertIn("morning BP pill", text)

    def test_med_due_sends_reminder(self):
        e = self.h.engine
        e.on_med_event({"type": "med.due", "level": "info",
                        "detail": {"med": "morning_bp_pill",
                                   "label": "morning BP pill",
                                   "window": ["07:30", "09:00"]}}, 60, NOW)
        kind, text = self.h.outbox_kinds()[-1]
        self.assertEqual(kind, "reminder")
        self.assertIn("morning BP pill", text)


if __name__ == "__main__":
    unittest.main()
