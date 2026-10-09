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

"""GPS routine awareness for the Muse HealthMesh hub prototype.

The iPhone's live location is SIMULATED here: ``scripted_places`` feeds a
sequence of (minute_offset, place) moves. Places use abstract demo coords,
not real addresses. On real hardware, the phone's raw GPS never leaves the
hub - only derived place labels (home/office/bar) are logged or shared.

Events emitted (returned by ``tick``):
  routine.place    (info)  - place changed; detail {"place", "prev"}
  routine.pattern  (info)  - office seen during 09:00-18:00 window
  routine.recovery (nudge) - 07:00 morning after a late night at the bar

Late-night logic: arriving at "bar" at hour >= 22 arms the late-night flag;
returning "home" afterwards schedules a recovery nudge for the next 07:00.
The nudge downgrades the day's workout to a walk and warns that HRV usually
dips after a late night.
"""

from __future__ import annotations

import random
from datetime import datetime

# Abstract demo coords, not real addresses; real GPS never leaves the hub,
# only place labels do.
PLACES = {
    "home": (0.0, 0.0),
    "office": (1.0, 0.2),
    "bar": (-0.6, 0.9),
}

OFFICE_START = 9   # 09:00
OFFICE_END = 18    # 18:00
LATE_NIGHT_HOUR = 22
RECOVERY_HOUR = 7

_RECOVERY_TEXT = (
    "Muse recovery: late night detected. Hydrate (500 ml water now), aim for "
    "a 20-min earlier bedtime tonight, and I've downgraded today's workout "
    "to a walk. Late nights usually dip your HRV tomorrow \u2014 I'll keep an "
    "eye on it."
)


class RoutineTracker:
    def __init__(self, seed: int = 7, scripted_places=None):
        self._rng = random.Random(seed)
        self._scripted = sorted(scripted_places or [], key=lambda e: e[0])
        self._place = None
        self._late_night = None       # {"arrived": datetime} or None
        self._recovery_pending = False

    def _scripted_place(self, minute: int):
        place = None
        for offset, name in self._scripted:
            if offset <= minute:
                place = name
            else:
                break
        return place

    def tick(self, now: datetime, minute: int) -> list:
        """Advance to ``now``; return event dicts (no ts)."""
        events = []
        place = self._scripted_place(minute)

        if place is not None and place != self._place:
            prev = self._place
            self._place = place
            events.append({
                "type": "routine.place",
                "level": "info",
                "detail": {"place": place, "prev": prev},
            })

            if place == "office" and OFFICE_START <= now.hour < OFFICE_END:
                events.append({
                    "type": "routine.pattern",
                    "level": "info",
                    "detail": {
                        "pattern": "office-hours",
                        "detail": "At office during 09:00-18:00 window",
                    },
                })

            if place == "bar" and now.hour >= LATE_NIGHT_HOUR:
                self._late_night = {"arrived": now}

            if place == "home" and self._late_night is not None:
                self._recovery_pending = True

        if self._recovery_pending and now.hour == RECOVERY_HOUR:
            arrived = self._late_night["arrived"] if self._late_night else None
            self._recovery_pending = False
            self._late_night = None
            events.append({
                "type": "routine.recovery",
                "level": "nudge",
                "detail": {
                    "arrived": arrived.isoformat() if arrived else None,
                    "note": "scheduled morning recovery after late night",
                },
            })

        return events

    def handle_event(self, event, minute, now, outbox, emit, ctx) -> None:
        """React to an emitted event; only writes to the outbox."""
        etype = event.get("type")
        if etype == "routine.place":
            if event.get("detail", {}).get("place") == "bar":
                outbox.send(
                    "info",
                    "Noted: evening out. I'll run a gentle recovery check "
                    "in the morning.",
                )
        elif etype == "routine.recovery":
            outbox.send("reminder", _RECOVERY_TEXT)
