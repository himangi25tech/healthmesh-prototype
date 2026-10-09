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

"""Muse HealthMesh hub prototype - main loop.

Simulates one hub tick per simulated minute: synthetic vitals, the
medication schedule, and the deterministic rules engine. Writes
``events.jsonl`` (event log), ``status.json`` (rewritten every tick) and
``outbox.jsonl`` (the ``musegadget send-user-msg`` shim).

Run from ``~/workspace/muse-gadget-sdk/linux``::

    python3 -m healthmesh.hub --scenario normal-day --speed 60
    python3 -m healthmesh.hub --scenario missed-dose --speed 0    # fast
    python3 -m healthmesh.hub --scenario night-spike --speed 0    # fast
    python3 -m healthmesh.hub --scenario grocery-day --speed 0    # phase-2: fridge + TV digest
    python3 -m healthmesh.hub --scenario late-night  --speed 0    # phase-2: GPS routine + recovery
    python3 -m healthmesh.hub --scenario full-day    --speed 0    # phase-2: everything + lab summary

``--speed`` is simulated minutes per real second; ``--speed 0`` disables
sleeping (fast verification mode). The three phase-2 scenarios exercise the
optional hub modules (kitchen, routines, tv, symptoms, labs,
healthkit_bridge); the original three scenarios set none of the phase-2
config keys, so their runs are unchanged.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

from .vitals import VitalsGenerator
from .meds import MedSchedule, MEDS
from .rules import RulesEngine
from .outbox import Outbox
from .kitchen import Fridge
from .routines import RoutineTracker
from .tv import TvNode
from .symptoms import SymptomJournal
from .labs import LabTracker
from .healthkit_bridge import HealthKitBridge

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_DATE = "2026-10-08"


def night_spike_script(minute: int) -> dict:
    """Scripted tachycardia: at 03:00 (minute 60) HR ramps to ~150 over
    10 minutes while HRV collapses. Returns signal overrides."""
    if minute < 60:
        return {}
    ramp = min((minute - 60) / 10.0, 1.0)
    wobble = 2.0 * math.sin(minute * 0.7) * ramp
    return {
        "hr": 62.0 + ramp * 90.0 + wobble,
        "hrv_ms": 16.0,
        "spo2": 94.0,
    }


SCENARIOS = {
    # Quiet morning: pill dispenses normally, nothing beyond info level.
    "normal-day": {
        "start": "06:00", "minutes": 240, "script": None,
        "misses": set(), "responses": {},
    },
    # Morning pill never dispenses: med.due 08:00 -> nudge 09:00 -> alert 09:30.
    "missed-dose": {
        "start": "07:00", "minutes": 210, "script": None,
        "misses": {"morning_bp_pill"}, "responses": {},
    },
    # 03:00 tachycardia, user does not respond: full emergency ladder.
    "night-spike": {
        "start": "02:00", "minutes": 120, "script": night_spike_script,
        "misses": set(), "responses": {},
    },
    # Phase-2: smart fridge day. Scripted scans drive low-stock nudges
    # (~08:00), a grapefruit/med interaction alert (~12:00) and an
    # end-of-run grocery order draft + TV digest.
    "grocery-day": {
        "start": "07:00", "minutes": 720, "script": None,
        "misses": set(), "responses": {},
        "fridge_scans": [
            (60, {"milk": -0.8, "oats": -400}),
            (300, {"eggs": -4, "bananas": -3, "grapefruit": 2}),
            (600, {"milk": -0.1}),
        ],
        "tv_digest_at": 719, "use_bridge": True,
    },
    # Phase-2: evening out. GPS places home -> bar (22:00) -> home (02:30);
    # the recovery nudge fires at the 07:00 tick.
    # NOTE: minutes is 661, not 660, so the run includes the 07:00 tick
    # (20:00 + 660 min = 07:00 next day); with 660 the loop would end at
    # 06:59 and the recovery nudge could never fire.
    "late-night": {
        "start": "20:00", "minutes": 661, "script": None,
        "misses": set(), "responses": {},
        "places": [(0, "home"), (120, "bar"), (390, "home")],
        "tv_digest_at": None, "use_bridge": False,
    },
    # Phase-2: everything combined — fridge scans, office routine, one
    # scripted symptom log, seeded demo lab entries and a TV digest at the
    # last tick. The lab entries are scripted demo data, not real results.
    "full-day": {
        "start": "06:00", "minutes": 1080, "script": None,
        "misses": set(), "responses": {},
        "fridge_scans": [
            (150, {"milk": -0.5, "oats": -200}),
            (420, {"eggs": -2}),
            (780, {"bananas": -2, "milk": -0.3}),
        ],
        "places": [(0, "home"), (180, "office"), (720, "home")],
        "symptom_logs": [(540, "headache", 4, "mild, after long screen session")],
        "labs_seed": True, "tv_digest_at": 1079, "use_bridge": True,
    },
}

# Optional phase-2 scenario keys. The original three scenarios set none of
# these, so their runs are behaviorally unchanged.
PHASE2_KEYS = ("fridge_scans", "places", "tv_digest_at", "symptom_logs",
               "labs_seed", "use_bridge")

# Scripted demo lab entries for the "labs_seed" scenarios. Clearly marked as
# demo data (suffix in the name shows up in doctor_summary.html); the emit
# event at seeding carries the same marker.
LAB_DEMO_ENTRIES = (
    ("LDL cholesterol (scripted demo)", 148, "mg/dL", 0, 100, "2026-09-20"),
    ("vitamin D (scripted demo)", 34, "ng/mL", 30, 100, "2026-09-20"),
    ("HbA1c (scripted demo)", 5.9, "%", 4.0, 5.6, "2026-09-20"),
)


def _dispatch_handle(module, event, minute, now, outbox, emit, ctx) -> None:
    """Call a module's ``handle_event`` under the shared module contract.

    The contract is ``handle_event(event, minute, now, outbox, emit, ctx)``.
    ``SymptomJournal`` is the one builder deviation — its ``handle_event``
    takes only ``event`` (the journal is user-driven, so this path never
    fires for it, but adapt anyway) — so fall back to the single-argument
    call when the module doesn't accept the full signature.
    """
    try:
        nparams = len(inspect.signature(module.handle_event).parameters)
    except (TypeError, ValueError):
        nparams = 6
    if nparams >= 6:
        module.handle_event(event, minute, now, outbox, emit, ctx)
    else:  # bound method, (event) only: SymptomJournal
        module.handle_event(event)


def _digest_text(scenario_name, med_states, fridge, recovery_fired,
                 has_labs) -> str:
    """One-line TV digest: scenario, med states, grocery count/recovery."""
    parts = ["Muse HealthMesh summary for scenario '%s'" % scenario_name]
    parts.append("meds: " + ", ".join("%s=%s" % kv
                                     for kv in med_states.items()))
    if fridge is not None:
        items = fridge.grocery_list()
        parts.append("grocery draft: %d item(s)%s" % (
            len(items),
            " (" + ", ".join(e["item"] for e in items) + ")" if items else ""))
    if recovery_fired:
        parts.append("recovery nudge sent after late night out")
    if has_labs:
        parts.append("3 scripted demo lab entries; doctor_summary.html ready")
    return ". ".join(parts) + "."


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Muse HealthMesh hub prototype (software-only demo).")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), required=True,
                   help="Which scripted scenario to run.")
    p.add_argument("--speed", type=float, default=1.0,
                   help="Simulated minutes per real second; 0 = no sleeping.")
    p.add_argument("--seed", type=int, default=7,
                   help="RNG seed for full determinism.")
    p.add_argument("--date", default=BASE_DATE,
                   help="Scenario date (YYYY-MM-DD).")
    p.add_argument("--contact", default="Aarav (brother)",
                   help="Emergency contact for the escalation ladder.")
    p.add_argument("--user-name", default="Himangi",
                   help="Name used in the emergency contact message.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.speed < 0:
        print("error: --speed must be >= 0", file=sys.stderr)
        return 2
    scenario = SCENARIOS[args.scenario]

    outbox_path = os.path.join(HERE, "outbox.jsonl")
    events_path = os.path.join(HERE, "events.jsonl")
    status_path = os.path.join(HERE, "status.json")
    tv_log_path = os.path.join(HERE, "tv_display.log")
    journal_path = os.path.join(HERE, "symptoms.jsonl")
    labs_path = os.path.join(HERE, "labs.json")
    for path in (outbox_path, events_path):
        open(path, "w").close()  # fresh log per run

    # Phase-2 modules activate only when the scenario sets one of the
    # optional keys. The original three scenarios set none, so their runs
    # follow the exact pre-phase-2 code path (including the real-time
    # outbox clock); phase-2 runs use a sim-time outbox clock so the same
    # seed + scenario gives a byte-identical outbox.jsonl.
    has_phase2 = any(k in scenario for k in PHASE2_KEYS)
    sim_clock = {"now": None}
    outbox = Outbox(
        outbox_path,
        clock=(lambda: sim_clock["now"] or datetime.now(timezone.utc))
        if has_phase2 else None,
    )
    events_fh = open(events_path, "a", encoding="utf-8")

    def emit(etype, level, detail, now):
        line = {"ts": now.isoformat(), "type": etype,
                "level": level, "detail": detail}
        events_fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        events_fh.flush()

    start = datetime.strptime(args.date + " " + scenario["start"], "%Y-%m-%d %H:%M")
    vitals_gen = VitalsGenerator(seed=args.seed, script_fn=scenario["script"])
    meds = MedSchedule(seed=args.seed, scripted_misses=scenario["misses"])
    rules = RulesEngine(outbox, emit, contact=args.contact,
                        user_name=args.user_name)

    # Phase-2 modules. TvNode + SymptomJournal exist whenever any phase-2
    # key is present (grocery-day/full-day need both; late-night needs the
    # TV node); the rest are keyed off their own config.
    modules = []
    fridge = tracker = tv = journal = labs = None
    recovery_fired = False
    if has_phase2:
        if scenario.get("use_bridge"):
            modules.append(HealthKitBridge())
        if "fridge_scans" in scenario:
            fridge = Fridge(seed=args.seed,
                            scripted_scans=scenario["fridge_scans"])
            modules.append(fridge)
        if "places" in scenario:
            tracker = RoutineTracker(seed=args.seed,
                                     scripted_places=scenario["places"])
            modules.append(tracker)
        open(tv_log_path, "w").close()  # fresh TV log per run
        tv = TvNode(outbox, tv_log_path)
        modules.append(tv)
        open(journal_path, "w").close()  # fresh journal per run
        journal = SymptomJournal(journal_path)
        modules.append(journal)
        if scenario.get("labs_seed"):
            if os.path.exists(labs_path):
                os.remove(labs_path)  # fresh demo entries per run
            labs = LabTracker(labs_path)
            for name, value, unit, ref_low, ref_high, date in LAB_DEMO_ENTRIES:
                labs.add_entry(name, value, unit, date, ref_low, ref_high)
            emit("lab.seeded", "info",
                 {"entries": len(LAB_DEMO_ENTRIES),
                  "note": "scripted demo data, not real lab results"}, start)

    symptom_schedule = {
        offset: (name, sev, note)
        for offset, name, sev, note in scenario.get("symptom_logs", [])
    }

    print("Muse HealthMesh hub prototype - scenario %r, seed %d, %s (+%d min)"
          % (args.scenario, args.seed, start.strftime("%Y-%m-%d %H:%M"),
             scenario["minutes"]), flush=True)
    print("Writing %s, %s, %s" % (outbox_path, events_path, status_path),
          flush=True)

    tick_sleep = 60.0 / args.speed if args.speed > 0 else 0.0

    med_states = {}
    ctx = None
    for minute in range(scenario["minutes"]):
        now = start + timedelta(minutes=minute)
        sim_clock["now"] = now
        vitals = vitals_gen.tick(now, minute)

        for event in meds.tick(now):
            emit(event["type"], event["level"], event["detail"], now)
            rules.on_med_event(event, minute, now)

        rules.evaluate(minute, vitals, now)

        # med_states is computed here (was: after check_timeouts) so the
        # phase-2 modules see the same snapshot the status line reports.
        # state() is a pure read; original scenarios are unaffected.
        med_states = {name: meds.state(name, now) for name in MEDS}

        if modules:
            ctx = {
                "vitals": vitals,
                "meds": med_states,
                "contact": args.contact,
                "user_name": args.user_name,
                # extras for SymptomJournal.check_patterns (ignored by the
                # other modules)
                "vitals_avg": dict(vitals),
                "meds_missed_24h": [n for n, s in med_states.items()
                                    if s == "missed"],
            }
            for module in modules:
                for event in module.tick(now, minute):
                    emit(event["type"], event["level"], event["detail"], now)
                    _dispatch_handle(module, event, minute, now, outbox,
                                     emit, ctx)
                    if event["type"] == "routine.recovery":
                        recovery_fired = True

        if journal is not None and (minute in symptom_schedule
                                    or minute % 60 == 0):
            if minute in symptom_schedule:
                name, sev, note = symptom_schedule[minute]
                journal.log(name, sev, note, now)
            # check_patterns takes emit(event) (single-arg); adapt it to
            # the hub's emit(etype, level, detail, now).
            journal.check_patterns(
                now, ctx, outbox,
                lambda event, _now=now: emit(event["type"], event["level"],
                                            event["detail"], _now))

        if minute in scenario["responses"]:
            rules.user_responded(minute, scenario["responses"][minute], now)

        rules.check_timeouts(minute, now)

        alert = rules.active_alert
        status = {
            "sim_time": now.isoformat(),
            "hr": vitals["hr"],
            "hrv_ms": vitals["hrv_ms"],
            "spo2": vitals["spo2"],
            "bp_sys": vitals["bp_sys"],
            "bp_dia": vitals["bp_dia"],
            "meds": med_states,
            "active_alert": alert,
        }
        with open(status_path, "w", encoding="utf-8") as fh:
            json.dump(status, fh, ensure_ascii=False, indent=2)

        alert_txt = ("%-9s %s" % (alert["level"].upper(), alert["text"])
                     if alert else "-")
        print("%s | HR %3d | HRV %3d ms | SpO2 %2d%% | BP %3d/%-3d | meds %s | %s"
              % (now.strftime("%Y-%m-%d %H:%M"), vitals["hr"], vitals["hrv_ms"],
                 vitals["spo2"], vitals["bp_sys"], vitals["bp_dia"],
                 ",".join("%s=%s" % kv for kv in med_states.items()), alert_txt),
              flush=True)

        if tick_sleep:
            time.sleep(tick_sleep)

    if fridge is not None:
        fridge.finalize(now, outbox, emit)

    if labs is not None:
        summary_path = os.path.join(HERE, "doctor_summary.html")
        labs.doctor_summary_html(
            summary_path, user_name=args.user_name,
            meds=[spec["label"] for spec in MEDS.values()])
        emit("lab.summary", "info", {"path": "doctor_summary.html"}, now)

    if tv is not None and scenario.get("tv_digest_at") is not None:
        tv.display_digest(_digest_text(args.scenario, med_states, fridge,
                                       recovery_fired, labs is not None), now)

    events_fh.close()
    outbox.close()
    print("Scenario %r complete." % args.scenario, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
