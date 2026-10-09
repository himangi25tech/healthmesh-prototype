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

"""Medication schedule + simulated smart pillbox for Muse HealthMesh.

Two daily doses: the morning BP pill (due 08:00, grace window 07:30-09:00)
and the evening vitamin (due 21:00, window 20:30-22:00). The simulated
pillbox normally dispenses ~10 minutes after the due time; scenarios can
script misses via ``scripted_misses``.

Events emitted (returned by ``tick``):
  med.due    (info)   - due time reached
  med.taken  (info)   - pillbox dispensed, or marked taken externally
  med.missed (nudge)  - grace window ended with no dispense
"""

from __future__ import annotations

import random
from datetime import datetime, time as dtime, timedelta

MEDS = {
    "morning_bp_pill": {
        "label": "morning BP pill",
        "due": dtime(8, 0),
        "window_start": dtime(7, 30),
        "window_end": dtime(9, 0),
        "dispense_delay_min": (8, 14),
    },
    "evening_vitamin": {
        "label": "evening vitamin",
        "due": dtime(21, 0),
        "window_start": dtime(20, 30),
        "window_end": dtime(22, 0),
        "dispense_delay_min": (5, 15),
    },
}

# Extra pre-window state reported by ``state()``; the shared contract's
# taken|due|missed values apply once the dose's window is under way.
STATES = ("pending", "due", "taken", "missed")


class MedSchedule:
    def __init__(self, seed: int = 7, scripted_misses=()):
        self._rng = random.Random(seed)
        self._misses = set(scripted_misses)
        self._doses = {}  # (date, med) -> {"state", "dispense_at"}

    def _dose(self, day, med):
        key = (day, med)
        dose = self._doses.get(key)
        if dose is None:
            dose = {"state": "pending", "dispense_at": None}
            self._doses[key] = dose
        return dose

    def tick(self, now: datetime) -> list:
        """Advance the schedule to ``now``; return event dicts (no ts)."""
        events = []
        day = now.date()
        for med, spec in MEDS.items():
            dose = self._dose(day, med)
            due_dt = datetime.combine(day, spec["due"])
            end_dt = datetime.combine(day, spec["window_end"])

            if dose["state"] == "pending" and now >= due_dt:
                dose["state"] = "due"
                events.append({
                    "type": "med.due",
                    "level": "info",
                    "detail": {
                        "med": med,
                        "label": spec["label"],
                        "window": [
                            spec["window_start"].strftime("%H:%M"),
                            spec["window_end"].strftime("%H:%M"),
                        ],
                    },
                })
                if med not in self._misses:
                    lo, hi = spec["dispense_delay_min"]
                    delay = self._rng.randint(lo, hi)
                    dose["dispense_at"] = due_dt + timedelta(minutes=delay)

            if (dose["state"] == "due" and dose["dispense_at"] is not None
                    and now >= dose["dispense_at"]):
                dose["state"] = "taken"
                events.append({
                    "type": "med.taken",
                    "level": "info",
                    "detail": {"med": med, "label": spec["label"],
                               "source": "smart-pillbox"},
                })

            if dose["state"] in ("pending", "due") and now >= end_dt:
                dose["state"] = "missed"
                events.append({
                    "type": "med.missed",
                    "level": "nudge",
                    "detail": {"med": med, "label": spec["label"],
                               "reason": "grace window ended without dispense"},
                })
        return events

    def mark_taken(self, med: str, now: datetime) -> dict | None:
        """External confirmation (maps to the future health.med_taken command)."""
        dose = self._dose(now.date(), med)
        if dose["state"] == "taken":
            return None
        dose["state"] = "taken"
        return {
            "type": "med.taken",
            "level": "info",
            "detail": {"med": med, "label": MEDS[med]["label"],
                       "source": "manual-confirm"},
        }

    def state(self, med: str, now: datetime) -> str:
        """Current dose state for status snapshots."""
        return self._dose(now.date(), med)["state"]
