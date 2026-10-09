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

"""Deterministic escalation engine for the Muse HealthMesh hub prototype.

This is the safety core: RULE-BASED ONLY, no LLM. Given vitals and
medication events it decides alert levels and drives the emergency ladder.
Alert levels are strictly ordered and never downgrade while a higher-severity
condition holds:

    info < nudge < alert < emergency
"""

from __future__ import annotations

LEVELS = ("info", "nudge", "alert", "emergency")
_RANK = {name: i for i, name in enumerate(LEVELS)}


def level_rank(level: str) -> int:
    return _RANK[level]


# ---------------------------------------------------------------------------
# Doctor-set thresholds. Change only with clinician sign-off; every value
# here is auditable and every firing is deterministic.
# ---------------------------------------------------------------------------
THRESHOLDS = {
    "hr_alert_bpm": 120,            # sustained -> alert
    "hr_alert_sustain_min": 5,
    "hr_emergency_bpm": 140,        # sustained -> emergency
    "hr_emergency_sustain_min": 3,
    "spo2_alert_pct": 92,           # sustained -> alert
    "spo2_alert_sustain_min": 3,
    "bp_sys_emergency_high": 180,   # immediate -> emergency
    "bp_sys_emergency_low": 90,     # immediate -> emergency
    "med_alert_after_miss_min": 30,  # missed dose: nudge at miss, alert +30 min
    "confirm_wait_min": 5,          # emergency ladder: wait for user response
}


class RulesEngine:
    """Evaluates vitals each tick and drives escalation.

    ``outbox``: an Outbox-like object with ``send(kind, text)``.
    ``emit``: callback ``emit(type, level, detail, now)`` writing events.jsonl.
    """

    def __init__(self, outbox, emit, contact: str = "Aarav (brother)",
                 user_name: str = "Himangi"):
        self._outbox = outbox
        self._emit = emit
        self._contact = contact
        self._user_name = user_name
        self._sustain = {"hr_alert": 0, "hr_emergency": 0, "spo2_alert": 0}
        self.active_alert = None  # None or {"level", "text", "source"}
        self._emergency = None    # None or {"deadline", "summary", "reason"}
        # Latched once an emergency ladder has run its course for the current
        # episode, so a still-critical patient is not re-paged every few
        # minutes. Cleared when the emergency condition itself clears, or
        # when the user responds.
        self._emergency_latched = False
        self._med_alerts = []     # [(due_minute, med, label)]

    # -- vitals evaluation -------------------------------------------------

    def evaluate(self, minute: int, vitals: dict, now) -> None:
        t = THRESHOLDS
        hr, spo2, sys = vitals["hr"], vitals["spo2"], vitals["bp_sys"]

        self._sustain["hr_alert"] = (
            self._sustain["hr_alert"] + 1 if hr >= t["hr_alert_bpm"] else 0)
        self._sustain["hr_emergency"] = (
            self._sustain["hr_emergency"] + 1 if hr >= t["hr_emergency_bpm"] else 0)
        self._sustain["spo2_alert"] = (
            self._sustain["spo2_alert"] + 1 if spo2 < t["spo2_alert_pct"] else 0)

        bp_emergency = sys >= t["bp_sys_emergency_high"] or sys <= t["bp_sys_emergency_low"]
        hr_emergency = self._sustain["hr_emergency"] >= t["hr_emergency_sustain_min"]
        hr_alert = self._sustain["hr_alert"] >= t["hr_alert_sustain_min"]
        spo2_alert = self._sustain["spo2_alert"] >= t["spo2_alert_sustain_min"]

        if self._emergency is not None:
            return  # ladder already running; nothing outranks it

        if bp_emergency or hr_emergency:
            if self._emergency is None and not self._emergency_latched:
                reason = ("blood pressure %d/%d mmHg out of safe range"
                          % (sys, vitals["bp_dia"]) if bp_emergency
                          else "heart rate %d bpm sustained" % hr)
                self._start_emergency(reason, vitals, minute, now)
            return

        # No emergency-level condition: the episode is over, unlatch so a
        # genuinely new event can fire the ladder again later.
        self._emergency_latched = False

        condition = None
        if hr_alert:
            condition = ("alert", "High heart rate: %d bpm sustained" % hr)
        elif spo2_alert:
            condition = ("alert", "Low blood oxygen: SpO2 %d%% sustained" % spo2)

        if condition:
            level, text = condition
            current = self.active_alert["level"] if self.active_alert else None
            if current is None or level_rank(level) > level_rank(current):
                self.active_alert = {"level": level, "text": text,
                                     "source": "vitals"}
                self._emit("vitals.anomaly", level,
                           {"condition": text, "vitals": vitals}, now)
                self._outbox.send("alert", "Muse alert: %s." % text)
        elif (self.active_alert and self.active_alert["level"] == "alert"
                and self.active_alert.get("source") == "vitals"):
            # Vitals condition cleared: stand the alert down. Alerts from
            # other sources (med escalation) are cleared by their own logic.
            self._emit("vitals.anomaly", "info",
                       {"status": "recovered", "vitals": vitals}, now)
            self.active_alert = None

    # -- medication escalation ----------------------------------------------

    def on_med_event(self, event: dict, minute: int, now) -> None:
        etype = event["type"]
        detail = event["detail"]
        if etype == "med.due":
            self._outbox.send(
                "reminder",
                "Reminder: time for your %s (window %s-%s)." % (
                    detail["label"], detail["window"][0], detail["window"][1]))
        elif etype == "med.missed" and event["level"] == "nudge":
            self._outbox.send(
                "reminder",
                "Reminder: your %s was missed. Please take it now if your "
                "care plan allows." % detail["label"])
            self._med_alerts.append(
                (minute + THRESHOLDS["med_alert_after_miss_min"],
                 detail["med"], detail["label"]))

    # -- emergency ladder ----------------------------------------------------

    def _start_emergency(self, reason: str, vitals: dict, minute: int, now) -> None:
        summary = ("HR %d bpm, SpO2 %d%%, BP %d/%d"
                   % (vitals["hr"], vitals["spo2"], vitals["bp_sys"], vitals["bp_dia"]))
        self.active_alert = {"level": "emergency",
                             "text": "Possible emergency: %s" % reason,
                             "source": "emergency"}
        self._emergency_latched = True
        self._emit("emergency.confirm_request", "emergency",
                   {"reason": reason, "vitals": vitals}, now)
        self._outbox.send(
            "alert",
            "Are you okay? HR %d looks abnormal \u2014 reply within 5 minutes."
            % vitals["hr"])
        self._emergency = {
            "deadline": minute + THRESHOLDS["confirm_wait_min"],
            "summary": summary,
            "reason": reason,
        }

    def user_responded(self, minute: int, text: str, now) -> bool:
        """A (scripted) user reply. Returns True if a ladder was cancelled."""
        self._emit("user.response", "info", {"text": text}, now)
        if self._emergency is None:
            return False
        self._emergency = None
        self.active_alert = None
        self._outbox.send(
            "info",
            "Emergency check cancelled \u2014 thanks for responding. "
            "Your Muse is standing down.")
        return True

    def check_timeouts(self, minute: int, now) -> None:
        for due, med, label in [a for a in self._med_alerts if a[0] <= minute]:
            self._med_alerts.remove((due, med, label))
            self._emit("med.missed", "alert",
                       {"med": med, "label": label,
                        "escalation": "still not taken 30 min after miss"}, now)
            self._outbox.send(
                "alert",
                "Muse alert: your %s is still not taken, 30 minutes after "
                "the missed window." % label)
            if self.active_alert is None:
                self.active_alert = {
                    "level": "alert",
                    "text": "Missed dose not taken: %s" % label,
                    "source": "meds"}

        if self._emergency is not None and minute >= self._emergency["deadline"]:
            summary = self._emergency["summary"]
            self._emit("emergency.no_response", "emergency",
                       {"waited_min": THRESHOLDS["confirm_wait_min"]}, now)
            self._emit("emergency.contacted", "emergency",
                       {"contact": self._contact, "channel": "whatsapp"}, now)
            self._outbox.send(
                "emergency",
                "WhatsApp to %s: %s may need help \u2014 %s at %s. "
                "Last known location: home." % (
                    self._contact, self._user_name, summary,
                    now.strftime("%Y-%m-%d %H:%M")))
            self._emergency = None
            self.active_alert = None
