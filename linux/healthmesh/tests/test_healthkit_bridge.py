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

"""Tests for healthkit_bridge.py.

``subprocess.run`` is faked with canned health-cli output so no real binary
is needed. The real-binary paths are exercised manually (see report).
"""

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from unittest import mock

from healthmesh.healthkit_bridge import HealthKitBridge
from healthmesh.outbox import Outbox
from healthmesh.vitals import VitalsGenerator

EMPTY_STATUS = json.dumps({
    "categories": [
        {"name": "daily-metrics", "record_count": 0,
         "earliest_datetime": None, "latest_datetime": None},
        {"name": "sleep", "record_count": 0,
         "earliest_datetime": None, "latest_datetime": None},
        {"name": "workout", "record_count": 0,
         "earliest_datetime": None, "latest_datetime": None},
    ]
})

FULL_STATUS = json.dumps({
    "categories": [
        {"name": "daily-metrics", "record_count": 42,
         "earliest_datetime": "2026-10-01", "latest_datetime": "2026-10-07"},
        {"name": "sleep", "record_count": 7,
         "earliest_datetime": "2026-10-01", "latest_datetime": "2026-10-07"},
        {"name": "workout", "record_count": 5,
         "earliest_datetime": "2026-10-01", "latest_datetime": "2026-10-07"},
    ]
})

METRICS_ENVELOPE = json.dumps({
    "coverage": {"complete": True},
    "records": [
        {"date": "2026-10-06", "resting_heart_rate": 61, "hrv": 58.5,
         "step_count": 8234, "sleep": 7.25, "active_calories": 412},
        {"date": "2026-10-07", "resting_hr": 58, "hrv_ms": 66,
         "steps": 9102, "sleep_hours": 8.0, "active_kcal": 388},
    ],
})

SLEEP_ENVELOPE = json.dumps({
    "coverage": {"complete": True},
    "records": [
        {"start": "2026-10-06T23:10:00", "end": "2026-10-07T06:40:00",
         "hours": 7.5, "quality": "good"},
    ],
})


def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.bridge = HealthKitBridge(cli_path="/fake/health-cli")

    def _patch(self, run_impl):
        return mock.patch("subprocess.run", side_effect=run_impl)

    # -- check() ---------------------------------------------------------
    def test_check_empty_status_disconnected(self):
        with self._patch(lambda *a, **k: _proc(EMPTY_STATUS)):
            status = self.bridge.check()
        self.assertFalse(status["connected"])
        self.assertEqual(status["categories"]["daily-metrics"], 0)
        self.assertEqual(status["categories"]["sleep"], 0)

    def test_check_status_with_records_connected(self):
        with self._patch(lambda *a, **k: _proc(FULL_STATUS)):
            status = self.bridge.check()
        self.assertTrue(status["connected"])
        self.assertEqual(status["categories"]["daily-metrics"], 42)
        self.assertEqual(status["categories"]["workout"], 5)

    def test_check_malformed_json_graceful(self):
        with self._patch(lambda *a, **k: _proc("this is not json {{{")):
            status = self.bridge.check()
        self.assertFalse(status["connected"])
        self.assertEqual(status["categories"], {})
        self.assertIn("invalid JSON", status["error"])

    def test_check_missing_binary_graceful(self):
        def boom(*a, **k):
            raise FileNotFoundError(2, "No such file")
        with self._patch(boom):
            status = self.bridge.check()
        self.assertFalse(status["connected"])
        self.assertIn("not found", status["error"])

    def test_check_empty_output_graceful(self):
        with self._patch(lambda *a, **k: _proc("", stderr="oops")):
            status = self.bridge.check()
        self.assertFalse(status["connected"])
        self.assertIn("no output", status["error"])

    def test_check_timeout_graceful(self):
        def slow(*a, **k):
            raise subprocess.TimeoutExpired(cmd="health-cli", timeout=15)
        with self._patch(slow):
            status = self.bridge.check()
        self.assertFalse(status["connected"])
        self.assertIn("timed out", status["error"])

    # -- fetch normalization ---------------------------------------------
    def test_fetch_daily_metrics_normalization(self):
        with self._patch(lambda *a, **k: _proc(METRICS_ENVELOPE)):
            env = self.bridge.fetch_daily_metrics("2026-10-01", "2026-10-07")
        self.assertTrue(env["connected"])
        self.assertEqual(len(env["records"]), 2)
        first, second = env["records"]
        # variant names on the first record map to the canonical ones
        self.assertEqual(first["date"], "2026-10-06")
        self.assertEqual(first["resting_hr"], 61)
        self.assertEqual(first["hrv_ms"], 58.5)
        self.assertEqual(first["steps"], 8234)
        self.assertEqual(first["sleep_hours"], 7.25)
        self.assertEqual(first["active_kcal"], 412)
        self.assertIn("raw", first)
        # canonical names on the second record pass through
        self.assertEqual(second["resting_hr"], 58)
        self.assertEqual(second["hrv_ms"], 66)

    def test_fetch_daily_metrics_empty(self):
        env_data = json.dumps({"coverage": {"complete": False}, "records": []})
        with self._patch(lambda *a, **k: _proc(env_data)):
            env = self.bridge.fetch_daily_metrics("2026-10-01", "2026-10-07")
        self.assertFalse(env["connected"])
        self.assertEqual(env["records"], [])

    def test_fetch_daily_metrics_bad_json(self):
        with self._patch(lambda *a, **k: _proc("garbage")):
            env = self.bridge.fetch_daily_metrics("2026-10-01", "2026-10-07")
        self.assertFalse(env["connected"])
        self.assertEqual(env["records"], [])
        self.assertIn("error", env)

    def test_fetch_sleep_sessions_normalization(self):
        with self._patch(lambda *a, **k: _proc(SLEEP_ENVELOPE)):
            env = self.bridge.fetch_sleep_sessions("2026-10-01", "2026-10-07")
        self.assertTrue(env["connected"])
        rec = env["records"][0]
        self.assertEqual(rec["start"], "2026-10-06T23:10:00")
        self.assertEqual(rec["end"], "2026-10-07T06:40:00")
        self.assertEqual(rec["hours"], 7.5)
        self.assertEqual(rec["quality"], "good")

    # -- hub vitals mapping ----------------------------------------------
    def test_to_hub_vitals_mapping(self):
        vitals = HealthKitBridge.to_hub_vitals(
            {"resting_hr": 60, "hrv_ms": 55})
        self.assertEqual(vitals, {"hr": 60, "hrv_ms": 55, "spo2": None,
                                  "bp_sys": None, "bp_dia": None})

    def test_to_hub_vitals_missing_fields_none(self):
        vitals = HealthKitBridge.to_hub_vitals({})
        self.assertEqual(vitals["hr"], None)
        self.assertEqual(vitals["hrv_ms"], None)

    # -- synthetic fallback -----------------------------------------------
    def test_synthetic_fallback_returns_working_generator(self):
        gen = HealthKitBridge.synthetic_fallback(seed=7)
        self.assertIsInstance(gen, VitalsGenerator)
        v1 = gen.tick(datetime(2026, 10, 7, 8, 0), 0)
        gen2 = HealthKitBridge.synthetic_fallback(seed=7)
        v2 = gen2.tick(datetime(2026, 10, 7, 8, 0), 0)
        self.assertEqual(v1, v2)  # seeded: deterministic
        self.assertEqual(set(v1), {"hr", "hrv_ms", "spo2", "bp_sys", "bp_dia"})

    # -- shared module contract --------------------------------------------
    def test_tick_emits_only_on_transitions(self):
        run = _proc(EMPTY_STATUS)
        with self._patch(lambda *a, **k: run):
            now = datetime(2026, 10, 7, 8, 0)
            # first check establishes the disconnected baseline silently
            self.assertEqual(self.bridge.tick(now, 0), [])
            # steady state stays quiet
            self.bridge._check_interval_min = 5
            self.assertEqual(self.bridge.tick(now, 1), [])
            self.assertEqual(self.bridge.tick(now, 5), [])

    def test_tick_disconnect_after_connect_emits_event(self):
        bridge = HealthKitBridge(cli_path="/fake/health-cli", check_interval_min=0)
        responses = {"status": FULL_STATUS}

        def run(*a, **k):
            argv = a[0]
            if "status" in argv:
                return _proc(responses["status"])
            if "sessions" in argv:
                return _proc(SLEEP_ENVELOPE)
            return _proc(METRICS_ENVELOPE)

        now = datetime(2026, 10, 7, 8, 0)
        with self._patch(run):
            self.assertEqual(bridge.tick(now, 0)[0]["type"], "healthkit.connected")
            responses["status"] = EMPTY_STATUS
            events = bridge.tick(now, 1)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["type"], "healthkit.disconnected")
            self.assertEqual(events[0]["level"], "nudge")
            self.assertFalse(bridge.is_connected)
            self.assertIsNone(bridge.latest_vitals)

    def test_tick_connected_transition_fetches_metrics(self):
        def run(*a, **k):
            argv = a[0]
            if "status" in argv:
                return _proc(FULL_STATUS)
            if "sessions" in argv:
                return _proc(SLEEP_ENVELOPE)
            return _proc(METRICS_ENVELOPE)
        with self._patch(run):
            now = datetime(2026, 10, 7, 8, 0)
            events = self.bridge.tick(now, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "healthkit.connected")
        self.assertTrue(self.bridge.is_connected)
        self.assertEqual(self.bridge.latest_vitals["hr"], 58)
        self.assertEqual(self.bridge.latest_vitals["hrv_ms"], 66)

    def test_handle_event_sends_outbox_message(self):
        import tempfile, os
        path = os.path.join(tempfile.mkdtemp(), "outbox.jsonl")
        outbox = Outbox(path)
        try:
            now = datetime(2026, 10, 7, 8, 0)
            ctx = {"user_name": "Himangi", "contact": "+911234567890",
                   "vitals": {"hr": 70}, "meds": {}}
            with self._patch(lambda *a, **k: _proc(EMPTY_STATUS)):
                self.bridge.handle_event(
                    {"type": "healthkit.disconnected"}, 0, now, outbox,
                    lambda *a: None, ctx)
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().strip().split("\n")
            self.assertEqual(len(lines), 1)
            line = json.loads(lines[0])
            self.assertEqual(line["kind"], "info")
            self.assertIn("Muse", line["text"])
            self.assertNotIn("Hatch", line["text"])
            # unrelated events -> no side-effect
            self.bridge.handle_event({"type": "med.due"}, 0, now, outbox,
                                     lambda *a: None, ctx)
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len(fh.read().strip().split("\n")), 1)
        finally:
            outbox.close()

    # -- CLI ---------------------------------------------------------------
    def test_cli_check_disconnected(self):
        with mock.patch("subprocess.run",
                        side_effect=lambda *a, **k: _proc(EMPTY_STATUS)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from healthmesh.healthkit_bridge import main
                rc = main(["--cli-path", "/fake/health-cli", "--check"])
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertIn("Connected: no", out)
        self.assertIn("Muse", out)
        self.assertNotIn("Hatch", out)

    def test_cli_metrics_empty(self):
        env_data = json.dumps({"coverage": {"complete": False,
                                            "warning": "not synced"},
                               "records": []})
        with mock.patch("subprocess.run",
                        side_effect=lambda *a, **k: _proc(env_data)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                from healthmesh.healthkit_bridge import main
                rc = main(["--cli-path", "/fake/health-cli", "--metrics",
                           "2026-10-01", "2026-10-07"])
        self.assertEqual(rc, 0)
        self.assertIn("no records", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
