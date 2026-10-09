# Test Muse HealthMesh on your own computer

The whole prototype is pure Python (standard library only) — no installs, no
hardware, no accounts needed. The ESP32 screen simulator is optional and needs
a C++ toolchain.

## What you need

- **Python 3.9 or newer.** Check: `python3 --version`
  - macOS: already installed. If missing, `xcode-select --install`.
  - Windows: install from python.org, then use `py` instead of `python3` below.
  - Linux: `sudo apt install python3` if missing.

## Run the Pi hub simulation

Unzip the package, then from the `linux/` folder inside it:

```sh
cd healthmesh-prototype/linux

# 1. Run the test suite (107 tests, ~1 second)
python3 -m unittest discover -s healthmesh/tests -t .

# 2. Run a demo day (pick one)
python3 -m healthmesh.hub --scenario night-spike --speed 0 --seed 7
python3 -m healthmesh.hub --scenario missed-dose --speed 0 --seed 7
python3 -m healthmesh.hub --scenario grocery-day  --speed 0 --seed 7
python3 -m healthmesh.hub --scenario late-night   --speed 0 --seed 7
python3 -m healthmesh.hub --scenario normal-day   --speed 0 --seed 7
python3 -m healthmesh.hub --scenario full-day     --speed 0 --seed 7
```

What each demo shows:

| Scenario | Watch for |
|---|---|
| `night-spike` | 3am heart-rate spike → "are you okay?" → emergency WhatsApp drafted |
| `missed-dose` | Morning pill missed → nudges escalate through the day |
| `grocery-day` | Fridge runs low → grocery list → order draft (incl. a grapefruit × BP-pill warning) |
| `late-night` | Bar night → 7am recovery protocol (hydration, workout downgrade) |
| `normal-day` | A quiet day — nothing fires (the control case) |
| `full-day` | Everything combined, incl. a sample doctor summary |

After a run, look at what the hub "sent":

```sh
cat healthmesh/outbox.jsonl    # messages that would go to you / contacts / TV
cat healthmesh/events.jsonl    # full event log of the simulated day
```

Each run writes into `healthmesh/` — delete the `.jsonl` files between runs if
you want a clean slate.

## Apple Watch bridge check (optional)

```sh
python3 -m healthmesh.healthkit_bridge --check
```

On your machine this will report "not connected" (the `health-cli` sync binary
only exists in Muse's workspace). That's expected — the hub falls back to
simulated vitals automatically. After you connect Apple Health in the Muse app
(Settings → Connectors → Apple Health), the same bridge reads your real
heart rate, HRV, and sleep. See `APPLE.md` for the full data path.

## ESP32 bedside screens (optional, heavier)

This builds the SDK's real desktop simulator (Linux/macOS only — not Windows).
Needs CMake 3.24+, Ninja, and a C compiler:

```sh
# macOS:  xcode-select --install; brew install cmake ninja
# Ubuntu: sudo apt install build-essential cmake ninja-build

cmake -S esp32/simulator -B esp32/simulator/build -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build esp32/simulator/build --parallel
```

Render the three HealthMesh screens without a display server:

```sh
./esp32/simulator/build/muse_simulator --headless \
  --scenario esp32/simulator/scenarios/healthmesh/vitals.txt \
  --screenshot vitals.ppm
# repeat with med-reminder.txt and alert.txt
```

Open the `.ppm` files in any image viewer (Preview on macOS opens them).
Pre-rendered PNGs are included in `esp32-simulator-scenarios/healthmesh/`.

## Files in this package

- `linux/healthmesh/` — the hub: vitals, meds, rules engine, HealthKit bridge,
  kitchen/fridge, routines, symptoms, labs, TV node, scenarios, tests
- `linux/healthmesh/APPLE.md` — how Apple Watch/iPhone data flows in
- `HEALTHMESH-PROTOTYPE.md` — full technical write-up
- `esp32-simulator-scenarios/healthmesh/` — bedside screen scenarios + renders
