# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.labs: trends, flags, doctor summary."""

import os
import tempfile
import unittest

from healthmesh.labs import LabTracker


class TestLabTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="healthmesh-labs-")
        self.tracker = LabTracker(os.path.join(self.tmp, "labs.json"))

    def add(self, name, value, date, lo, hi, unit="mg/dL"):
        return self.tracker.add_entry(name, value, unit, date, lo, hi)

    def test_add_entry_returns_and_persists(self):
        entry = self.add("LDL cholesterol", 130, "2026-09-01", 0, 100)
        self.assertEqual(entry["name"], "LDL cholesterol")
        self.assertEqual(entry["value"], 130)
        self.assertEqual(self.tracker.latest("LDL cholesterol"), entry)
        # Re-loaded from disk.
        again = LabTracker(os.path.join(self.tmp, "labs.json"))
        self.assertEqual(again.latest("LDL cholesterol")["value"], 130)

    def test_latest_none_for_unknown(self):
        self.assertIsNone(self.tracker.latest("missing"))

    def test_trend_insufficient(self):
        self.assertEqual(self.tracker.trend("vitamin D"), "insufficient")
        self.add("vitamin D", 30, "2026-09-01", 30, 100, "ng/mL")
        self.assertEqual(self.tracker.trend("vitamin D"), "insufficient")

    def test_trend_up(self):
        self.add("triglycerides", 100, "2026-03-01", 0, 150)
        self.add("triglycerides", 120, "2026-09-01", 0, 150)  # +20%
        self.assertEqual(self.tracker.trend("triglycerides"), "up")

    def test_trend_down(self):
        self.add("HbA1c", 6.4, "2026-03-01", 4.0, 5.6, "%")
        self.add("HbA1c", 5.9, "2026-09-01", 4.0, 5.6, "%")  # -7.8%
        self.assertEqual(self.tracker.trend("HbA1c"), "down")

    def test_trend_stable(self):
        self.add("HDL cholesterol", 55, "2026-03-01", 40, 100)
        self.add("HDL cholesterol", 57, "2026-09-01", 40, 100)  # +3.6%
        self.assertEqual(self.tracker.trend("HDL cholesterol"), "stable")

    def test_flag_above_range(self):
        self.add("LDL cholesterol", 150, "2026-09-01", 0, 100)
        flags = self.tracker.flags()
        self.assertIn({"name": "LDL cholesterol", "issue": "above range"}, flags)

    def test_flag_below_range(self):
        self.add("vitamin D", 22, "2026-09-01", 30, 100, "ng/mL")
        flags = self.tracker.flags()
        self.assertIn({"name": "vitamin D", "issue": "below range"}, flags)

    def test_no_flag_in_range(self):
        self.add("total cholesterol", 180, "2026-09-01", 125, 200)
        self.assertEqual(self.tracker.flags(), [])

    def test_flag_sharp_rise(self):
        self.add("fasting glucose", 95, "2026-03-01", 70, 100)
        self.add("fasting glucose", 125, "2026-09-01", 70, 100)  # +31.6%
        flags = self.tracker.flags()
        self.assertIn({"name": "fasting glucose", "issue": "sharp rise"}, flags)
        # Also out of range (125 > 100).
        self.assertIn({"name": "fasting glucose", "issue": "above range"}, flags)

    def test_flag_sharp_drop(self):
        self.add("hemoglobin", 14.0, "2026-03-01", 12.0, 16.0, "g/dL")
        self.add("hemoglobin", 10.5, "2026-09-01", 12.0, 16.0, "g/dL")  # -25%
        flags = self.tracker.flags()
        self.assertIn({"name": "hemoglobin", "issue": "sharp drop"}, flags)

    def test_no_sharp_flag_on_small_move(self):
        self.add("HbA1c", 6.4, "2026-03-01", 4.0, 5.6, "%")
        self.add("HbA1c", 5.9, "2026-09-01", 4.0, 5.6, "%")  # -7.8%
        issues = [f["issue"] for f in self.tracker.flags()
                  if f["name"] == "HbA1c"]
        self.assertNotIn("sharp drop", issues)
        self.assertIn("above range", issues)

    def test_doctor_summary_html_contents(self):
        self.add("LDL cholesterol", 150, "2026-09-01", 0, 100)
        self.add("vitamin D", 22, "2026-09-01", 30, 100, "ng/mL")
        self.add("HDL cholesterol", 57, "2026-09-01", 40, 100)
        path = os.path.join(self.tmp, "summary.html")
        result = self.tracker.doctor_summary_html(
            path, user_name="Himangi",
            meds=["morning BP pill (08:00)", "evening vitamin (21:00)"])
        self.assertEqual(result, path)
        with open(path, encoding="utf-8") as fh:
            doc = fh.read()
        self.assertIn("Muse HealthMesh", doc)
        self.assertIn("LDL cholesterol", doc)
        self.assertIn("Questions for your doctor", doc)
        self.assertIn("Himangi", doc)
        self.assertIn("morning BP pill", doc)
        self.assertIn("\u26a0", doc)  # out-of-range marker present
        self.assertIn("above range", doc)


if __name__ == "__main__":
    unittest.main()
