<!--
Copyright (c) Meta Platforms, Inc. and affiliates.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Muse HealthMesh — ESP32 node firmware plan

Production firmware plan for the ESP32 nodes in the Muse HealthMesh system.
**This is a plan only — no firmware changes are made in this prototype.**
The simulator scenarios in
`esp32/simulator/scenarios/healthmesh/` are the visual proof of what each node
displays for each hub state.

## Roles

| Role | Board | Why |
|---|---|---|
| Bedside display node | Waveshare ESP32-S3-Touch-AMOLED-1.75(C) | Full Muse UI, push-to-talk, settings, images (see `esp32/README.md` boards table) |
| Voice-only node | Home Assistant Voice Preview Edition | LED-ring status, push-to-talk, volume dial; replies render in the Muse app |
| (Later) wrist node | M5Stack StopWatch or StickS3 | Full UI in a wearable form factor |

All naming visible to the user says **Muse**, never Hatch
(`esp32/AGENTS.md`: "Say Muse, never Hatch" — screen text, captions, error
captions like `CAN'T REACH MUSE`, settings labels, Kconfig prompts, log
lines, tool output, docs).

## Custom health commands

HealthMesh adds gadget commands following the exact pattern in
`esp32/AGENTS.md` ("Adding a command"):

1. **Advertise** in `build_register_json()` in `main/noise_control.cpp` with
   `add_command(commands, "<name>", "<description>", required, optional)`.
2. **Handle** in `on_ws_command()` in `main/app.c`. Return
   `{"ok": true, "payload": {...}}` or `command_error(code, message)`.
   Always return a result; validate parameters yourself.

Commands (names TBD with the hub team):

- `health.vitals` — current vitals snapshot the node should display
  (hr, hrv_ms, spo2, bp_sys, bp_dia).
- `health.meds` — medication schedule entries due/updated.
- `health.alert` — raise/clear an alert `{level, text}`; the node maps it to
  a face and caption.

Rules from AGENTS.md that apply: don't block `on_ws_command()` (it runs on
the Noise session task — slow work goes in its own task, copy
`request_id`/`session_generation`/params into task-owned memory, return
`{"_async": true}`), keep `link.register` under 8 KB, gate
hardware-specific commands on Kconfig options in `main/Kconfig.projbuild`,
and add host tests in `tests/`.

## Spoken answers

Replies from Muse are text; nodes that should *speak* reminders and alerts
hook `start_tts()` in `components/muse/muse_chat_session.cpp`
(per `esp32/README.md`, "Spoken answers"): it has the reply text, and the MP3
decoder, speaker and volume are already wired up there. The function body
documents the recipe — send the reply text to a TTS API, pass the returned
MP3 to `tts_data()`, set `s_turn.mp3_ended` at the end, and the player
follows at the speaker's volume with captions.

Med reminders ("Time for your morning BP pill") and escalation alerts use
the `speaking` face with a short caption; alert levels map to faces:

| Hub `active_alert.level` | Node face | Caption example |
|---|---|---|
| none | `idle` | `HR 72 · SpO2 98% · 120/80` |
| info | `idle` / `happy` | short status line |
| nudge | `speaking` | `Time for your morning BP pill` |
| alert | `error` | `Heart rate high — are you okay?` |
| emergency | `error` + speaker | `EMERGENCY — CALLING` |

## Consuming hub state

- **Prototype (now):** the Pi hub writes `linux/healthmesh/status.json` each
  tick:
  `{sim_time, hr, hrv_ms, spo2, bp_sys, bp_dia, meds:{...}, active_alert:
  null|{level,text}}`. The simulator scenario files in
  `esp32/simulator/scenarios/healthmesh/` statically demonstrate each display
  state the hub can produce. Caption convention: short vitals line like
  `HR 72 · SpO2 98% · 120/80` — it renders on small watch/board screens.
- **Production (later):** this state arrives over the gadget link as
  `health.*` command invocations (see above), and the hub reads node state
  back through the same link. The status.json file is the dev stand-in, not
  the wire format.

## Simulator proof

From the repo root:

```sh
cmake -S esp32/simulator -B esp32/simulator/build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build esp32/simulator/build --parallel
./esp32/simulator/build/muse_simulator --headless \
  --scenario esp32/simulator/scenarios/healthmesh/vitals.txt --run-ms 250 \
  --screenshot vitals.ppm
```

Scenarios: `vitals.txt` (bedside vitals card, `idle` face, wifi connected,
link online), `med-reminder.txt` (`speaking` face), `alert.txt` (`error`
face). Valid keys and values are documented in `esp32/simulator/README.md`.
