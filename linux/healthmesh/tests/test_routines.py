# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for healthmesh.routines: place changes, office pattern, bar nights."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta

from healthmesh.outbox import Outbox
from healthmesh.routines import RoutineTracker

START = datetime(2026, 10, 7, 8, 0)  # a Wednesday morning


def drive(tracker, start, minutes):
    """Tick minute-by-minute; return the full emitted event stream."""
    events = []
    for m in range(minutes):
        now = start + timedelta(minutes=m)
        for ev in tracker.tick(now, m):
            events.append({"ts": now.isoformat(), **ev})
    return events


class TestRoutines(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.box = Outbox(os.path.join(self.tmp, "outbox.jsonl"),
                          clock=lambda: START)
        self.emit_events = []

    def emit(self, etype, level, detail, now):
        self.emit_events.append({"type": etype, "level": level,
                                 "detail": detail})

    def handle_all(self, tracker, events, start):
        for ev in events:
            minute = int((datetime.fromisoformat(ev["ts"]) - start)
                         .total_seconds() // 60)
            tracker.handle_event(ev, minute,
                                 datetime.fromisoformat(ev["ts"]),
                                 self.box, self.emit, {})
        self.box.close()

    def read_outbox(self):
        with open(os.path.join(self.tmp, "outbox.jsonl"), encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_place_changes_fire_in_order(self):
        tracker = RoutineTracker(scripted_places=[
            (0, "home"), (60, "office"), (300, "bar"), (360, "home")])
        events = drive(tracker, START, 400)
        places = [e["detail"]["place"] for e in events
                  if e["type"] == "routine.place"]
        self.assertEqual(places, ["home", "office", "bar", "home"])
        self.assertEqual(events[0]["detail"]["prev"], None)
        self.assertEqual(events[1]["detail"]["prev"], "home")
        for e in events:
            if e["type"] == "routine.place":
                self.assertEqual(e["level"], "info")

    def test_office_hours_pattern(self):
        # office at 09:00 -> inside the 09:00-18:00 window
        tracker = RoutineTracker(scripted_places=[(0, "home"), (60, "office")])
        events = drive(tracker, START, 120)
        patterns = [e for e in events if e["type"] == "routine.pattern"]
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["level"], "info")
        self.assertEqual(patterns[0]["detail"]["pattern"], "office-hours")
        self.assertEqual(patterns[0]["detail"]["detail"],
                         "At office during 09:00-18:00 window")

    def test_no_office_pattern_outside_window(self):
        # office at 19:00 -> outside the window, no pattern event
        tracker = RoutineTracker(scripted_places=[(0, "home"), (660, "office")])
        events = drive(tracker, START, 720)
        self.assertEqual([e for e in events
                          if e["type"] == "routine.pattern"], [])

    def test_bar_night_recovery_at_0700(self):
        # office 09:00 -> bar 23:00 -> home 02:30 -> recovery at 07:00
        tracker = RoutineTracker(scripted_places=[
            (0, "home"),
            (60, "office"),     # 09:00
            (900, "bar"),       # 23:00
            (1110, "home"),     # next day 02:30
        ])
        events = drive(tracker, START, 1500)  # through 09:00 next day
        recoveries = [e for e in events if e["type"] == "routine.recovery"]
        self.assertEqual(len(recoveries), 1)
        rec = recoveries[0]
        self.assertEqual(rec["level"], "nudge")
        self.assertEqual(datetime.fromisoformat(rec["ts"]).hour, 7)
        self.assertEqual(datetime.fromisoformat(rec["ts"]).minute, 0)

        self.handle_all(tracker, events, START)
        texts = [line["text"] for line in self.read_outbox()]
        recovery = [t for t in texts if "Muse recovery" in t]
        self.assertEqual(len(recovery), 1)
        self.assertIn("500 ml", recovery[0])      # hydration
        self.assertIn("HRV", recovery[0])          # HRV note
        # bar arrival also sent the evening-out info note
        self.assertTrue(any("evening out" in t for t in texts))

    def test_no_bar_no_recovery(self):
        tracker = RoutineTracker(scripted_places=[
            (0, "home"), (60, "office"), (600, "home")])
        events = drive(tracker, START, 1500)
        self.assertEqual([e for e in events
                          if e["type"] == "routine.recovery"], [])
        self.handle_all(tracker, events, START)
        texts = [line["text"] for line in self.read_outbox()]
        self.assertFalse(any("Muse recovery" in t for t in texts))

    def test_bar_info_note_sent_on_bar_arrival(self):
        tracker = RoutineTracker(scripted_places=[(0, "home"), (900, "bar")])
        events = drive(tracker, START, 960)
        bar = [e for e in events
               if e["type"] == "routine.place"
               and e["detail"]["place"] == "bar"]
        self.assertEqual(len(bar), 1)
        self.handle_all(tracker, bar, START)
        kinds_texts = [(l["kind"], l["text"]) for l in self.read_outbox()]
        self.assertIn(("info",
                       "Noted: evening out. I'll run a gentle recovery check "
                       "in the morning."), kinds_texts)


if __name__ == "__main__":
    unittest.main()
