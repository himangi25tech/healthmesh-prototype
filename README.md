# Muse HealthMesh — software prototype

https://muse.ai/s/healthmesh-readme-axk6lgouxgxrxlxg

A runnable, hardware-free prototype of the 24/7 personal health assistant
("Muse HealthMesh", see the product blueprint at
`~/workspace/your_files/build-blueprint/build-blueprint.pdf`). No ESP32
boards, no Raspberry Pi, no Bluetooth, no Muse app pairing, no SDK token —
everything here runs on this machine. All prototype code is **new files
only**; no upstream SDK file was modified (`git status` shows just two new
untracked directories: `linux/healthmesh/` and
`esp32/simulator/scenarios/`).

**Safety framing:** this is a wellness-assistant prototype, not a medical
device. Thresholds in `rules.py` are placeholders marked "doctor-set".

## What was built

```
linux/healthmesh/                  Pi-hub prototype (Python 3, stdlib only)
  vitals.py                        Seeded synthetic vitals (HR, HRV, SpO2, BP),
                                   1 tick = 1 sim-minute, circadian rhythm
  meds.py                          Med schedule: morning BP pill 08:00,
                                   evening vitamin 21:00; scriptable pillbox
  rules.py                         Deterministic rule-based escalation engine
                                   (the safety core — no LLM anywhere)
  outbox.py                        Shim for `musegadget send-user-msg`
  hub.py                           Main loop + CLI + terminal dashboard;
                                   phase-2: optional module wiring + 3 new
                                   scenarios (grocery-day, late-night, full-day)
  kitchen.py                       Smart-fridge simulation: scripted scans,
                                   low-stock detection, med-aware grocery
                                   order drafts, grapefruit/med interaction flags
  routines.py                      GPS routine awareness: scripted place moves
                                   (home/office/bar), late-night detection and
                                   the 07:00 recovery nudge
  tv.py                            Android TV overlay node: logs what would be
                                   shown to tv_display.log + outbox kind
                                   tv_display (alerts and digest cards)
  symptoms.py                      Symptom journal (JSONL): explicit log() calls
                                   plus headache/fatigue pattern correlation
                                   rules (suggestions, never diagnoses)
  labs.py                          Biomarker tracking: ref-range + sharp-move
                                   flags, and a one-page doctor_summary.html
                                   for visit prep
  healthkit_bridge.py              Apple Watch/iPhone bridge via health-cli:
                                   connection checks, daily metrics + sleep
                                   fetch, synthetic fallback when disconnected
  APPLE.md                         Apple data path: Watch → iPhone Health →
                                   health-cli → bridge; connect steps, field
                                   mapping, before/after comparison
  COMMANDS.md                      Future real-SDK commands in COMMAND_SPECS style
  esp32-node.md                    ESP32 production firmware plan
  tests/                           107 stdlib unittest tests (all pass),
                                   incl. test_kitchen / test_routines /
                                   test_tv / test_symptoms / test_labs /
                                   test_healthkit_bridge for the phase-2 modules
  outbox.jsonl                     Runtime artifact (rewritten each run)
  events.jsonl                     Runtime artifact (rewritten each run)
  status.json                      Runtime artifact (rewritten each tick)
  tv_display.log                   Runtime artifact: TV overlay contract log
                                   (rewritten each phase-2 run)
  symptoms.jsonl                   Runtime artifact: symptom journal
                                   (rewritten each phase-2 run)
  labs.json                        Runtime artifact: lab entries (reset when a
                                   scenario seeds demo entries)
  doctor_summary.html              Runtime artifact: visit summary (full-day)

esp32/simulator/scenarios/healthmesh/
  vitals.txt / .ppm / .png         Bedside vitals card (idle face)
  med-reminder.txt / .ppm / .png   Med reminder (speaking face)
  alert.txt / .ppm / .png          High-HR alert (error face)
```

## Run the demos

Hub (from `~/workspace/muse-gadget-sdk/linux`):

```sh
python3 -m healthmesh.hub --scenario normal-day  --speed 0 --seed 7
python3 -m healthmesh.hub --scenario missed-dose --speed 0 --seed 7
python3 -m healthmesh.hub --scenario night-spike  --speed 0 --seed 7
python3 -m healthmesh.hub --scenario grocery-day  --speed 0 --seed 7
python3 -m healthmesh.hub --scenario late-night   --speed 0 --seed 7
python3 -m healthmesh.hub --scenario full-day     --speed 0 --seed 7
```

`--speed 0` = as fast as possible (verification mode). For a live feel use
`--speed 60` (60 sim-minutes per real second). `--seed 7` is the default;
same seed + same scenario = byte-identical run (phase-2 scenarios use a
sim-time outbox clock so `outbox.jsonl` diffs byte-identical too).

Expected outcomes:

| Scenario | Window | What happens |
|---|---|---|
| `normal-day` | 06:00–10:00 | Pill taken 08:10; only `info` events; no alerts |
| `missed-dose` | 07:00–10:30 | `med.due` 08:00 → nudge 09:00 → alert 09:30 |
| `night-spike` | 02:00–04:00 | HR 151+ at 03:11 → confirm request → no response → emergency WhatsApp line in outbox |
| `grocery-day` | 07:00–19:00 | Low-stock nudges (milk, oats) ~08:00; grapefruit/med interaction alert ~12:00; `grocery_order` draft (milk, oats) at end; TV digest overlay at 18:59 |
| `late-night` | 20:00–07:00+1 | home → bar 22:00 ("evening out" note) → home 02:30 → recovery nudge at 07:00 |
| `full-day` | 06:00–24:00 | Office routine 09:00–18:00; fridge scans; headache logged 15:00; milk low-stock nudge 19:00; 3 scripted demo lab entries → `doctor_summary.html` generated; TV digest overlay at 23:59 |

Tests:

```sh
cd ~/workspace/muse-gadget-sdk && python3 -m unittest discover -s linux/healthmesh/tests -t linux
# Ran 107 tests ... OK
```

ESP32 simulator (from repo root; needs `ninja` on PATH — on this VM a
static ninja lives at `~/workspace/tools/ninja`; CMake auto-downloads pinned
SDL 2.32.10 + LVGL 9.5.0 when no system SDL2 exists):

```sh
cmake -S esp32/simulator -B esp32/simulator/build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build esp32/simulator/build --parallel
./esp32/simulator/build/muse_simulator --headless \
  --scenario esp32/simulator/scenarios/healthmesh/alert.txt \
  --run-ms 250 --screenshot /tmp/alert-check.ppm
```

(Replace `alert.txt` with `vitals.txt` / `med-reminder.txt` for the others.)

## The hub/node contract

Both sides implement this; it becomes the real wire protocol later:

- `linux/healthmesh/outbox.jsonl` — stands in for
  `musegadget send-user-msg`. Lines: `{ts, session_id:"healthmesh-demo",
  kind:"info|reminder|alert|emergency", text}`.
- `linux/healthmesh/events.jsonl` — hub event log:
  `{ts, type, level:"info|nudge|alert|emergency", detail}`.
- `linux/healthmesh/status.json` — rewritten each tick: current vitals, med
  states, `active_alert` (null or `{level, text, source}`).
- Alert ordering is strict: `info < nudge < alert < emergency`.
- `active_alert.level` → node face: none→`idle`, info→`idle`/`happy`,
  nudge→`speaking`, alert→`error`, emergency→`error` + speaker.

Two deliberate contract extensions (documented in code): med states include
`pending` before the due window; `active_alert` carries `source`
(`vitals|meds|emergency`) so a meds alert isn't cleared by vitals recovery.

## Simulated vs real

| Prototype (now) | Real hardware (later) |
|---|---|
| Synthetic vitals (`vitals.py`) | Watch via HealthKit/Health Connect; BLE BP cuff |
| Scripted pillbox (`meds.py`) | ESP32 + load-cell pillbox |
| `outbox.jsonl` shim | `musegadget send-user-msg` → paired Muse app |
| `status.json` file | Gadget link messages (`health.*` commands) |
| Fixed demo contact "Aarav (brother)" | Real emergency contacts + WhatsApp |
| Placeholder thresholds | Doctor-set thresholds |
| `health.log_symptom` spec'd only | Symptom journal in hub |
| Apple Health data (bridge → synthetic vitals until connected) | Real records via `health-cli` once Apple Health is connected |
| Fridge camera re-scans (`kitchen.py` scripted scans) | ESP32 camera node |
| GPS routine tracking (`routines.py` scripted places) | iPhone location via hub |
| TV overlay (`tv_display.log` + outbox `tv_display` lines) | Android TV Remote2 / Home Assistant `androidtv` integration |
| Symptom journal (`symptoms.py` `log()` method calls) | Push-to-talk + transcription on the voice node |
| Lab entries (manual `add_entry()` / scripted demo seed) | Photo OCR of lab reports |

## Apple connect checklist

Apple Watch/iPhone data reaches the hub through `healthkit_bridge.py`
(full data path and field mapping in `linux/healthmesh/APPLE.md`).

**Exact steps to connect**
1. On the iPhone, open the **Muse app → Settings → Connectors → Apple
   Health** and tap **Connect**.
2. When iOS asks for Health permissions, grant at minimum **Heart Rate**,
   **Heart Rate Variability (HRV)**, **Steps**, and **Sleep** (Blood
   Pressure and SpO₂ help if logged manually in Health).
3. Open the **Health app** once and pull-to-refresh to trigger the first
   on-device sync.

**How to verify** (from `~/workspace/muse-gadget-sdk/linux`):
```sh
python3 -m healthmesh.healthkit_bridge --check
```
Expect `Connected: yes` with non-zero per-category record counts. If it
prints `Connected: no`, the bridge stays on synthetic vitals.

**What changes after connecting**
- Before: bridge reports disconnected (silently — it only announces state
  *transitions*); hub vitals come from the seeded `VitalsGenerator`.
- After: `hr` comes from resting heart rate, `hrv_ms` from HRV; SpO₂/BP
  stay synthetic (Apple Watch doesn't report those reliably to HealthKit).
- The rules engine, meds, kitchen, TV and other modules are unchanged —
  they consume the same hub vitals dict either way.
- `python3 -m healthmesh.healthkit_bridge --metrics 2026-10-01 2026-10-07`
  prints the normalized daily metrics the hub would use.

## When hardware arrives — checklist

**Pi hub**
- [ ] `bash install.sh --sdk-token mgst_…` on the Pi; pair in the Muse app
      (Settings > Devices > Developer mode) — see `linux/README.md`
- [ ] Port `linux/healthmesh/` logic into real gadget commands: add
      `health.vitals_snapshot`, `health.log_symptom`, `health.med_taken`,
      `health.med_schedule` to `COMMAND_SPECS` in
      `linux/src/musegadget/executor.py` (+ `Executor.run` branch + test in
      `tests/test_executor.py`), per `linux/AGENTS.md` "Adding a command"
      and the specs in `linux/healthmesh/COMMANDS.md`
- [ ] Replace `outbox.py` shim with real `musegadget send-user-msg` calls
- [ ] Feed real vitals (watch skills, BLE cuff) into `rules.py`; keep the
      rules engine deterministic and local
- [ ] Set thresholds + emergency contacts with her doctor; decide the
      confirm-timeout and no-response policy before enabling `emergency`

**ESP32 nodes**
- [ ] Get an SDK token from gadgets.muse.ai; flash per `esp32/README.md`
      (`tools/board.sh <board> build|flash`), e.g. Waveshare ESP32-S3 AMOLED
      as bedside display, Home Assistant Voice PE as voice node
- [ ] Add `health.vitals` / `health.meds` / `health.alert` via
      `link.register` in `main/app.c` (pattern: `esp32/AGENTS.md` "Adding a
      command"); spoken alerts via the `start_tts` hook in
      `components/muse/muse_chat_session.cpp` — full plan in
      `linux/healthmesh/esp32-node.md`
- [ ] Build the smart pillbox node (ESP32 + load cells) as a new board
      profile

**Data & safety**
- [ ] Connect watch data (Apple HealthKit / Google Health Connect skills)
- [ ] Enable NVS encryption on ESP32 boards (`CONFIG_HOMEHUB_NVS_ENCRYPTION`)
- [ ] Weekly review loop: vitals trends → Muse summary → doctor questions
