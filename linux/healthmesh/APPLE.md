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

# Apple data path — Apple Watch → Muse HealthMesh hub

Apple Watch sensors (heart rate, HRV, workouts) and the iPhone's own motion
coprocessor (steps) write into the **iPhone Health app**. The phone syncs that
data on-device to the Muse runtime, where `/opt/hatch/bin/health-cli` exposes
it. The bridge (`healthkit_bridge.py`) reads `health-cli` and turns Apple
Health records into hub vitals.

```
Apple Watch ──bluetooth──▶ iPhone Health app ──on-device sync──▶ health-cli
        └─ HealthKit store (Heart, HRV, Sleep, Steps) ─┘          │
                                                                 ▼
                                                        HealthKitBridge
                                                     (check / fetch / normalize)
                                                                 │
                                                                 ▼
                                                        hub vitals {"hr","hrv_ms",...}
```

## Connecting (exact steps)

1. On the iPhone, open the **Muse app → Settings → Connectors → Apple Health**
   and tap **Connect**.
2. When iOS asks for Health permissions, grant at minimum **Heart Rate**,
   **Heart Rate Variability (HRV)**, **Steps**, and **Sleep**. Granting Blood
   Pressure and SpO₂ helps if you log them manually in Health.
3. Open the **Health app** once and pull-to-refresh: this triggers the first
   sync of the on-device store.
4. Verify from the hub device:
   `python3 -m healthmesh.healthkit_bridge --check` (run from
   `~/workspace/muse-gadget-sdk/linux`). It should print `Connected: yes`
   with non-zero record counts.

Sync is incremental afterwards — new workouts and nightly sleep appear after
the phone syncs them.

## Field mapping (HealthKit concept → hub field)

| HealthKit concept              | Bridge field   | Hub vitals field | Notes                                        |
|--------------------------------|----------------|------------------|----------------------------------------------|
| Resting Heart Rate             | `resting_hr`   | `hr`             | resting value, not live per-second HR        |
| Heart Rate Variability (SDNN)  | `hrv_ms`       | `hrv_ms`         | milliseconds                                 |
| Step Count                     | `steps`        | —                | kept in normalized record, not a hub signal  |
| Sleep Analysis (total)         | `sleep_hours`  | —                | hours per day; sessions also fetched         |
| Active Energy                  | `active_kcal`  | —                | kcal per day                                 |
| Blood Oxygen (SpO₂)            | —              | `spo2` = None    | Apple Watch reports this sporadically; the hub treats `None` as "no data, keep synthetic" |
| Blood Pressure                 | —              | `bp_sys`/`bp_dia` = None | no HealthKit BP from Apple Watch; stays `None` unless a paired cuff writes to Health |

Variant field names are tolerated defensively: `resting_heart_rate` and
`heart_rate` both map to `resting_hr`; `hrv` → `hrv_ms`; `step_count` →
`steps`; `sleep` → `sleep_hours`; `active_calories`/`active_energy` →
`active_kcal`. Unknown shapes are kept under the record's `raw` key.

## Before connecting vs after connecting

| | Before connecting (default) | After connecting |
|---|---|---|
| Connection status | `Connected: no`, all categories 0 records | `Connected: yes`, counts > 0 |
| Vitals source | Seeded `VitalsGenerator` (deterministic, circadian, scenario-scriptable) | Real data: `hr` from resting HR, `hrv_ms` from HRV; SpO₂/BP stay `None` → synthetic values kept for those |
| Event log | `healthkit.disconnected` once (nudge) | `healthkit.connected` once (info); no repeated chatter |
| Rules engine | **Unchanged** — same thresholds, same escalation ladder, same outbox | **Unchanged** |
| Meds / kitchen / TV modules | Unchanged | Unchanged |

The hub never sees a difference between sources: it consumes the same
`{"hr","hrv_ms","spo2","bp_sys","bp_dia"}` dict either way.

## Troubleshooting

- **`Connected: no`, zero records everywhere** — Apple Health is not linked.
  Run the Connect steps above, grant permissions on the iPhone, and open the
  Health app to trigger a sync. Then re-run `--check`.
- **Query prints "no records" with a coverage warning** (`coverage.complete:
  false`) — the data window has not synced from the device. The warning text
  from `health-cli` names the missing dates and the backfill action to run;
  re-run the query after the backfill.
- **`--metrics` fails with "health-cli not found"** — the bridge expects the
  binary at `/opt/hatch/bin/health-cli`. Override with
  `--cli-path /path/to/health-cli`.
- **Records look wrong (e.g. HR as a per-second stream)** — the metrics query
  returns daily aggregates; raw per-sample data is available under the
  record's `raw` payload if you need it, but the hub only consumes the
  normalized daily fields.
