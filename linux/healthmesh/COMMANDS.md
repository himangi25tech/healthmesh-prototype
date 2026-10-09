# Copyright (c) Meta Platforms, Inc. and affiliates.
<!-- Licensed under the Apache License, Version 2.0. -->

# Muse HealthMesh — future gadget commands

Command specs for the real Muse Gadget SDK integration, written in the
`COMMAND_SPECS` style from `linux/src/musegadget/executor.py`
(`description`, `required` / `optional` params each with `type` and
`description`, plus `timeout_ms` where the default 30 s is wrong).

Safety boundary (from the HealthMesh blueprint): the Muse may explain, ask,
or summarize, but only the deterministic rules core may trigger an emergency
workflow or a device action. These commands are read/write affordances for
that core — none of them changes an escalation threshold or contact list.

```python
COMMAND_SPECS = {
    "health.vitals_snapshot": {
        "description": (
            "Return the latest vitals snapshot from the hub's rules core: "
            "heart rate, HRV, SpO2, blood pressure, the current medication "
            "states, and any active alert. Read-only; never triggers "
            "escalation by itself."
        ),
        "required": {},
        "optional": {
            "window_min": {
                "type": "integer",
                "description": (
                    "Include per-minute history for the last N minutes "
                    "(1-60). Default 0 (snapshot only)."
                ),
            },
        },
    },
    "health.log_symptom": {
        "description": (
            "Record a user-reported symptom in the hub journal with a "
            "timestamp (e.g. 'dizzy', 'chest tightness'). Journal entries "
            "are context for the Muse's conversation only; they never "
            "change rules thresholds or fire escalation."
        ),
        "required": {
            "symptom": {
                "type": "string",
                "description": "Short user description of the symptom.",
            },
        },
        "optional": {
            "severity": {
                "type": "integer",
                "description": "Self-reported severity, 1 (mild) to 5 (severe).",
            },
            "note": {
                "type": "string",
                "description": "Free-text context, e.g. what the user was doing.",
            },
        },
    },
    "health.med_taken": {
        "description": (
            "Confirm a scheduled dose was taken (voice confirmation, "
            "manual tap, pillbox event). Updates the dose state so the "
            "missed-dose escalation ladder stands down."
        ),
        "required": {
            "med": {
                "type": "string",
                "description": (
                    "Medication id from health.med_schedule, e.g. "
                    "'morning_bp_pill'."
                ),
            },
        },
        "optional": {
            "at": {
                "type": "string",
                "description": (
                    "ISO-8601 timestamp of the dose. Default: now."
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "How the dose was confirmed: 'voice', 'manual', "
                    "'pillbox'. Default 'manual'."
                ),
            },
        },
    },
    "health.med_schedule": {
        "description": (
            "Read the medication schedule: each dose's due time, grace "
            "window, and current state (pending, due, taken, missed). "
            "Read-only."
        ),
        "required": {},
        "optional": {
            "date": {
                "type": "string",
                "description": "Schedule date as YYYY-MM-DD. Default: today.",
            },
        },
    },
}
```

## Prototype mapping

| Future command              | Prototype equivalent (this directory)                              |
|-----------------------------|---------------------------------------------------------------------|
| `health.vitals_snapshot`    | `vitals.py` `VitalsGenerator.tick()` + `status.json` each hub tick |
| `health.log_symptom`        | not yet prototyped (would append to `events.jsonl` as `user.symptom`) |
| `health.med_taken`          | `meds.py` `MedSchedule.mark_taken()`                               |
| `health.med_schedule`       | `meds.py` `MEDS` table + `MedSchedule.state()`                     |

Outbound user messages in the prototype go through `outbox.py`, the shim
for `musegadget send-user-msg`; on a real device those become
`POST /chat/stream` calls on the paired session (`session_id`
`healthmesh-demo` here). The escalation ladder itself (`rules.py`) stays
rule-based and local in both worlds.
