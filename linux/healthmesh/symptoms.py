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

"""Symptom journal for the Muse HealthMesh hub prototype.

On hardware this is a voice push-to-talk flow on the ESP32 node; in the
prototype a symptom is logged with a plain method call. The journal is
user-driven: ``tick`` never emits anything on its own.

``check_patterns`` runs two simple correlation rules (suggestions, never
diagnoses) over the journal plus hub context, each firing at most once
per 24h:

  headache_pattern (info) - a headache/migraine entry >= 6/10 in the last
    24h while a med was missed in the same window.
  fatigue_pattern (info)  - a fatigue entry >= 6/10 in the last 24h while
    the vitals average shows under 6h of sleep.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

HEADACHE_NAMES = ("headache", "migraine")
FATIGUE_NAMES = ("fatigue",)
_PATTERN_WINDOW = timedelta(hours=24)

# Message templates (user-visible; "Muse" branding, suggestion wording).
_HEADACHE_MSG = (
    "Muse note: {name} ({sev}/10) logged, and {label} was missed in the "
    "last 24h \u2014 consider whether they\u2019re related (not a diagnosis)."
)
_FATIGUE_MSG = (
    "Muse note: fatigue {sev}/10 with {hours}h sleep \u2014 rest up today; "
    "worth mentioning to your doctor if it persists."
)


class SymptomJournal:
    def __init__(self, path):
        self._path = path
        # rule_key -> datetime it last fired (throttle: once per 24h)
        self._last_fired = {}

    # -- hub loop integration ------------------------------------------------

    def tick(self, now, minute) -> list:
        """The journal is user-driven; it never emits autonomous events."""
        return []

    def handle_event(self, event) -> None:
        """Symptoms are logged explicitly; inbound events change nothing."""
        return None

    # -- logging -------------------------------------------------------------

    def log(self, name, severity, note, now) -> dict:
        """Append a symptom entry to the journal JSONL; return the entry."""
        if not isinstance(severity, int) or isinstance(severity, bool) \
                or not 1 <= severity <= 10:
            raise ValueError("severity must be an int 1..10, got %r" % (severity,))
        entry = {
            "ts": now.isoformat(),
            "name": name.lower(),
            "severity": severity,
            "note": note,
        }
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def _read_all(self) -> list:
        entries = []
        if not os.path.exists(self._path):
            return entries
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def recent(self, hours, now) -> list:
        """Entries logged within the last ``hours`` hours."""
        cutoff = now - timedelta(hours=hours)
        out = []
        for entry in self._read_all():
            try:
                ts = datetime.fromisoformat(entry["ts"])
            except (KeyError, ValueError, TypeError):
                continue
            if ts >= cutoff:
                out.append(entry)
        return out

    # -- correlation rules ---------------------------------------------------

    def _due(self, rule_key, now) -> bool:
        """True if ``rule_key`` may fire (at most once per 24h)."""
        last = self._last_fired.get(rule_key)
        return last is None or (now - last) >= _PATTERN_WINDOW

    def check_patterns(self, now, ctx, outbox, emit) -> list:
        """Run the two correlation rules; return the emitted event dicts."""
        emitted = []
        vitals_avg = ctx.get("vitals_avg") or {}
        meds_missed_24h = ctx.get("meds_missed_24h") or []
        window = self.recent(24, now)

        def _match(names, min_sev):
            hits = [e for e in window
                    if e.get("name") in names and e.get("severity", 0) >= min_sev]
            return max(hits, key=lambda e: e.get("severity", 0)) if hits else None

        # Rule 1: severe headache/migraine + missed med in last 24h.
        hit = _match(HEADACHE_NAMES, 6)
        if hit and meds_missed_24h and self._due("headache_pattern", now):
            label = meds_missed_24h[0]
            outbox.send("info", _HEADACHE_MSG.format(
                name=hit["name"].capitalize(), sev=hit["severity"], label=label))
            event = {"type": "symptom.pattern", "level": "info",
                     "detail": {"rule": "headache_pattern", "entry": hit,
                                "missed_med": label}}
            emit(event)
            emitted.append(event)
            self._last_fired["headache_pattern"] = now

        # Rule 2: severe fatigue + under 6h sleep.
        hit = _match(FATIGUE_NAMES, 6)
        sleep = vitals_avg.get("sleep_hours")
        if hit and sleep is not None and sleep < 6 \
                and self._due("fatigue_pattern", now):
            outbox.send("info", _FATIGUE_MSG.format(
                sev=hit["severity"], hours=sleep))
            event = {"type": "symptom.pattern", "level": "info",
                     "detail": {"rule": "fatigue_pattern", "entry": hit,
                                "sleep_hours": sleep}}
            emit(event)
            emitted.append(event)
            self._last_fired["fatigue_pattern"] = now

        return emitted
