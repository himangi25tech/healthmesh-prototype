# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.meds: schedule, windows, scripted misses."""

import unittest
from datetime import datetime, timedelta

from healthmesh.meds import MedSchedule

DAY = datetime(2026, 10, 8)


def at(h, m):
    return DAY.replace(hour=h, minute=m)


class TestMedSchedule(unittest.TestCase):
    def test_due_then_taken(self):
        sched = MedSchedule(seed=7)
        events = sched.tick(at(8, 0))
        self.assertEqual([e["type"] for e in events], ["med.due"])
        self.assertEqual(events[0]["level"], "info")
        self.assertEqual(sched.state("morning_bp_pill", at(8, 5)), "due")
        # Pillbox dispenses 8-14 min after due; sweep forward.
        seen = []
        for minute in range(1, 30):
            seen += sched.tick(at(8, 0) + timedelta(minutes=minute))
        taken = [e for e in seen if e["type"] == "med.taken"]
        self.assertEqual(len(taken), 1)
        self.assertEqual(taken[0]["level"], "info")
        self.assertEqual(sched.state("morning_bp_pill", at(8, 30)), "taken")

    def test_scripted_miss_emits_missed_at_window_end(self):
        sched = MedSchedule(seed=7, scripted_misses={"morning_bp_pill"})
        self.assertEqual(sched.tick(at(8, 0))[0]["type"], "med.due")
        self.assertEqual(sched.tick(at(8, 59)), [])
        missed = sched.tick(at(9, 0))
        self.assertEqual([e["type"] for e in missed], ["med.missed"])
        self.assertEqual(missed[0]["level"], "nudge")
        self.assertEqual(sched.state("morning_bp_pill", at(9, 1)), "missed")
        # Missed fires exactly once.
        self.assertEqual(sched.tick(at(9, 30)), [])

    def test_evening_vitamin_schedule(self):
        sched = MedSchedule(seed=7)
        sched.tick(at(8, 0))  # settle the morning dose first
        events = sched.tick(at(21, 0))
        evening = [e for e in events if e["detail"]["med"] == "evening_vitamin"]
        self.assertEqual([e["type"] for e in evening], ["med.due"])
        self.assertEqual(evening[0]["detail"]["window"], ["20:30", "22:00"])

    def test_determinism(self):
        def run():
            sched = MedSchedule(seed=7)
            out = []
            now = at(6, 0)
            for _ in range(300):
                out += [(e["type"], e["level"]) for e in sched.tick(now)]
                now += timedelta(minutes=1)
            return out
        self.assertEqual(run(), run())

    def test_mark_taken(self):
        sched = MedSchedule(seed=7)
        ev = sched.mark_taken("morning_bp_pill", at(7, 45))
        self.assertEqual(ev["type"], "med.taken")
        self.assertEqual(ev["detail"]["source"], "manual-confirm")
        self.assertEqual(sched.state("morning_bp_pill", at(7, 46)), "taken")
        # Due tick afterwards must not re-emit.
        self.assertEqual(sched.tick(at(8, 0)), [])


if __name__ == "__main__":
    unittest.main()
