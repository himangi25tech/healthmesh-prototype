# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.vitals: determinism, circadian shape, smoothness."""

import unittest
from datetime import datetime, timedelta

from healthmesh.vitals import VitalsGenerator


def run_sequence(seed=7, start=None, minutes=240, script_fn=None):
    gen = VitalsGenerator(seed=seed, script_fn=script_fn)
    start = start or datetime(2026, 10, 8, 6, 0)
    return [gen.tick(start + timedelta(minutes=m), m) for m in range(minutes)]


class TestVitals(unittest.TestCase):
    def test_determinism_same_seed_identical_sequences(self):
        a = run_sequence(seed=7)
        b = run_sequence(seed=7)
        self.assertEqual(a, b)

    def test_different_seed_differs(self):
        a = run_sequence(seed=7)
        b = run_sequence(seed=8)
        self.assertNotEqual(a, b)

    def test_night_hr_lower_than_midday_hr(self):
        seq = run_sequence(start=datetime(2026, 10, 8, 0, 0), minutes=24 * 60)
        night = [t["hr"] for t in seq[2 * 60:4 * 60]]
        midday = [t["hr"] for t in seq[12 * 60:14 * 60]]
        avg = lambda xs: sum(xs) / len(xs)
        self.assertLess(avg(night), 62, "night HR should dip near ~55")
        self.assertGreater(avg(midday), 70, "midday HR should sit 70-78")
        self.assertLess(avg(night), avg(midday))

    def test_no_wild_jumps(self):
        seq = run_sequence(minutes=240)
        steps = [abs(b["hr"] - a["hr"]) for a, b in zip(seq, seq[1:])]
        self.assertLessEqual(max(steps), 8,
                             "unsmoothed jumps would break the rules engine")

    def test_ranges_sane(self):
        seq = run_sequence(minutes=24 * 60)
        for t in seq:
            self.assertGreaterEqual(t["spo2"], 95)
            self.assertLessEqual(t["spo2"], 100)
            self.assertGreaterEqual(t["hr"], 45)
            self.assertLessEqual(t["hr"], 110)
            self.assertGreaterEqual(t["hrv_ms"], 10)

    def test_script_override_applies(self):
        seq = run_sequence(minutes=10,
                           script_fn=lambda m: {"hr": 150.0} if m >= 5 else {})
        self.assertLess(seq[4]["hr"], 100)
        self.assertEqual(seq[5]["hr"], 150)
        self.assertEqual(seq[9]["hr"], 150)


if __name__ == "__main__":
    unittest.main()
