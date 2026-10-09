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

"""Synthetic vitals generator for the Muse HealthMesh hub prototype.

One tick == one simulated minute. The output is smooth and circadian:

* night dip: HR ~55 around 03:00-04:00
* morning rise: ~07:00-08:00
* daytime: 70-78 with light activity noise
* SpO2: 96-99, BP ~115-125/75-85 with a morning surge

All randomness comes from a seeded ``random.Random``, so runs are fully
deterministic. There are no wild jumps except through scripted scenario
events, injected via ``script_fn(minute_index)`` which returns a dict of
signal overrides (e.g. the night-spike tachycardia).
"""

from __future__ import annotations

import math
import random
from datetime import datetime

# Circadian anchor points for resting heart rate (hour of day -> bpm).
_HR_ANCHORS = (
    (0.0, 58.0),
    (3.0, 55.0),
    (4.0, 55.5),
    (6.0, 62.0),
    (7.0, 69.0),
    (8.0, 74.0),
    (12.0, 76.0),
    (17.0, 75.0),
    (20.0, 70.0),
    (22.0, 63.0),
    (24.0, 58.0),
)


def _circadian_hr(hour: float) -> float:
    h = hour % 24.0
    for (h0, v0), (h1, v1) in zip(_HR_ANCHORS, _HR_ANCHORS[1:]):
        if h0 <= h <= h1:
            t = (h - h0) / (h1 - h0)
            t = t * t * (3.0 - 2.0 * t)  # smoothstep: no kinks at anchors
            return v0 + (v1 - v0) * t
    return 58.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


class VitalsGenerator:
    """Seeded, smooth synthetic vitals. ``tick(now, minute_index)`` -> dict."""

    def __init__(self, seed: int = 7, script_fn=None):
        self._rng = random.Random(seed)
        self._script_fn = script_fn
        self._hr = None
        self._hrv = None
        self._spo2 = None
        self._sys = None
        self._dia = None
        self._activity = 0.0

    def tick(self, now: datetime, minute_index: int) -> dict:
        rng = self._rng
        hour = now.hour + now.minute / 60.0

        # Daytime activity: occasional bumps that decay away (smoothed below).
        if 7.0 <= hour <= 22.0 and rng.random() < 0.02:
            self._activity += rng.uniform(4.0, 10.0)
        self._activity *= 0.985

        target_hr = _circadian_hr(hour) + self._activity + rng.gauss(0.0, 1.1)
        if self._hr is None:
            self._hr = target_hr
        else:
            self._hr += 0.3 * (target_hr - self._hr)

        # HRV runs inversely to HR: higher at night, collapses under strain.
        target_hrv = _clamp(72.0 - (self._hr - 55.0) * 1.1, 12.0, 95.0)
        target_hrv += rng.gauss(0.0, 1.5)
        if self._hrv is None:
            self._hrv = target_hrv
        else:
            self._hrv += 0.25 * (target_hrv - self._hrv)

        target_spo2 = 97.5 + rng.gauss(0.0, 0.15)
        if self._spo2 is None:
            self._spo2 = target_spo2
        else:
            self._spo2 = _clamp(self._spo2 + 0.2 * (target_spo2 - self._spo2), 95.0, 99.5)

        # Blood pressure: gentle morning surge, tracks HR a little.
        surge = 10.0 * math.exp(-(((hour - 8.5) / 1.5) ** 2))
        target_sys = 118.0 + surge + (self._hr - 70.0) * 0.12 + rng.gauss(0.0, 0.8)
        target_dia = 78.0 + surge * 0.5 + (self._hr - 70.0) * 0.08 + rng.gauss(0.0, 0.6)
        if self._sys is None:
            self._sys, self._dia = target_sys, target_dia
        else:
            self._sys += 0.25 * (target_sys - self._sys)
            self._dia += 0.25 * (target_dia - self._dia)

        # Scripted scenario events override the smoothed signals directly.
        if self._script_fn is not None:
            override = self._script_fn(minute_index) or {}
            if "hr" in override:
                self._hr = float(override["hr"])
            if "hrv_ms" in override:
                self._hrv = float(override["hrv_ms"])
            if "spo2" in override:
                self._spo2 = float(override["spo2"])
            if "bp_sys" in override:
                self._sys = float(override["bp_sys"])
            if "bp_dia" in override:
                self._dia = float(override["bp_dia"])

        return {
            "hr": int(round(self._hr)),
            "hrv_ms": int(round(self._hrv)),
            "spo2": int(round(self._spo2)),
            "bp_sys": int(round(self._sys)),
            "bp_dia": int(round(self._dia)),
        }
