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

"""Apple Watch / iPhone Health data bridge for Muse HealthMesh.

Data path: Apple Watch -> iPhone Health app -> on-device sync -> ``health-cli``
-> this bridge -> hub vitals.

The bridge works against real data when present and degrades gracefully when
empty: every query path catches subprocess errors and malformed JSON, treats
missing/zero records as "disconnected", and never raises on empty data. When
disconnected, :meth:`synthetic_fallback` hands back the seeded
``VitalsGenerator`` so demos and the rules engine keep working unchanged.

Module contract (shared with all hub modules):
  ``tick(now, minute)`` -> list of event dicts. The bridge checks connection
  state on the first tick and then at most every ``check_interval_min``
  simulated minutes, emitting ``healthkit.connected`` / ``healthkit.disconnected``
  events only on state transitions.
  ``handle_event(event, minute, now, outbox, emit, ctx)`` -> None; sends the
  user-visible outbox message for those transitions.

Run from ``~/workspace/muse-gadget-sdk/linux``::

    python3 -m healthmesh.healthkit_bridge --check
    python3 -m healthmesh.healthkit_bridge --metrics 2026-10-01 2026-10-07
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta

from .vitals import VitalsGenerator

DEFAULT_CLI = "/opt/hatch/bin/health-cli"
DEFAULT_TIMEOUT = 15

METRIC_KEYS = {
    "resting_hr": ("resting_hr", "resting_heart_rate", "heart_rate"),
    "hrv_ms": ("hrv_ms", "hrv"),
    "steps": ("steps", "step_count"),
    "sleep_hours": ("sleep_hours", "sleep"),
    "active_kcal": ("active_kcal", "active_calories", "active_energy"),
}


def _first(rec: dict, *names):
    """First present, non-None value among ``names``; None if all absent."""
    for name in names:
        value = rec.get(name)
        if value is not None:
            return value
    return None


class HealthKitBridge:
    """Reads Apple Health data through ``health-cli`` into hub vitals."""

    def __init__(self, cli_path: str = DEFAULT_CLI, timeout: int = DEFAULT_TIMEOUT,
                 check_interval_min: int = 60, days_back: int = 7):
        self._cli = cli_path
        self._timeout = timeout
        self._check_interval_min = check_interval_min
        self._days_back = days_back
        self._connected = False
        self._last_check_minute = None
        self._records = []          # last fetched normalized daily metrics
        self._sleep_records = []    # last fetched normalized sleep sessions
        self._latest_vitals = None   # hub vitals dict from the newest record

    # ------------------------------------------------------------------
    # low-level CLI access: every failure mode returns a dict, never raises
    # ------------------------------------------------------------------
    def _run(self, *argv):
        """Run health-cli; return (ok, parsed_dict_or_None, error_str)."""
        try:
            proc = subprocess.run(
                [self._cli, *argv],
                capture_output=True, text=True, timeout=self._timeout,
            )
        except FileNotFoundError:
            return False, None, "health-cli not found at %s" % self._cli
        except subprocess.TimeoutExpired:
            return False, None, "health-cli timed out after %ss" % self._timeout
        except OSError as exc:
            return False, None, "health-cli failed: %s" % exc
        out = (proc.stdout or "").strip()
        if not out:
            err = (proc.stderr or "").strip()[:200]
            return False, None, "health-cli returned no output%s" % (
                (": " + err) if err else "")
        try:
            return True, json.loads(out), ""
        except json.JSONDecodeError as exc:
            return False, None, "health-cli returned invalid JSON: %s" % exc

    # ------------------------------------------------------------------
    # public queries
    # ------------------------------------------------------------------
    def check(self) -> dict:
        """Connection status -> {"connected", "categories", "raw"} (+ "error")."""
        ok, data, error = self._run("status", "--provider", "healthkit")
        if not ok:
            return {"connected": False, "categories": {}, "raw": {},
                    "error": error}
        data = data if isinstance(data, dict) else {}
        categories = {}
        for cat in (data.get("categories") or []):
            if isinstance(cat, dict) and cat.get("name") is not None:
                try:
                    categories[cat["name"]] = int(cat.get("record_count") or 0)
                except (TypeError, ValueError):
                    categories[cat["name"]] = 0
        connected = any(count > 0 for count in categories.values())
        result = {"connected": connected, "categories": categories, "raw": data}
        if not connected:
            result["error"] = "no health records synced"
        return result

    def _query(self, *argv):
        """Query envelope -> {"connected", "records", "coverage"} (+ "error")."""
        ok, data, error = self._run(*argv)
        if not ok:
            return {"connected": False, "records": [], "coverage": None,
                    "error": error}
        data = data if isinstance(data, dict) else {}
        records = data.get("records")
        if not isinstance(records, list):
            records = []
        return {"connected": bool(records), "records": records,
                "coverage": data.get("coverage")}

    @staticmethod
    def _normalize_metric(rec: dict) -> dict:
        return {
            "date": rec.get("date"),
            "resting_hr": _first(rec, *METRIC_KEYS["resting_hr"]),
            "hrv_ms": _first(rec, *METRIC_KEYS["hrv_ms"]),
            "steps": _first(rec, *METRIC_KEYS["steps"]),
            "sleep_hours": _first(rec, *METRIC_KEYS["sleep_hours"]),
            "active_kcal": _first(rec, *METRIC_KEYS["active_kcal"]),
            "raw": rec,
        }

    def fetch_daily_metrics(self, start_date: str, end_date: str) -> dict:
        """Daily metrics envelope -> {"connected", "records": [normalized]}."""
        env = self._query("query", "metrics", "--provider", "healthkit",
                          "--start-date", start_date, "--end-date", end_date)
        records = [self._normalize_metric(r) for r in env["records"]
                   if isinstance(r, dict)]
        env["records"] = records
        env["connected"] = bool(records)
        return env

    @staticmethod
    def _normalize_sleep(rec: dict) -> dict:
        return {
            "start": _first(rec, "start", "start_time", "start_datetime"),
            "end": _first(rec, "end", "end_time", "end_datetime"),
            "hours": _first(rec, "hours", "sleep_hours", "duration_hours"),
            "quality": _first(rec, "quality", "sleep_quality"),
            "raw": rec,
        }

    def fetch_sleep_sessions(self, start_date: str, end_date: str) -> dict:
        """Sleep sessions envelope -> {"connected", "records": [normalized]}."""
        env = self._query("query", "sessions", "--provider", "healthkit",
                          "--category", "sleep",
                          "--start-date", start_date, "--end-date", end_date)
        records = [self._normalize_sleep(r) for r in env["records"]
                   if isinstance(r, dict)]
        env["records"] = records
        env["connected"] = bool(records)
        return env

    # ------------------------------------------------------------------
    # hub integration
    # ------------------------------------------------------------------
    @staticmethod
    def to_hub_vitals(record: dict) -> dict:
        """Normalized metrics record -> hub vitals dict.

        ``resting_hr`` maps to ``hr``; fields absent from HealthKit (SpO2, BP)
        stay None, which the hub treats as "no data, keep synthetic".
        """
        return {
            "hr": record.get("resting_hr"),
            "hrv_ms": record.get("hrv_ms"),
            "spo2": None,
            "bp_sys": None,
            "bp_dia": None,
        }

    @staticmethod
    def synthetic_fallback(seed: int = 7) -> VitalsGenerator:
        """Seeded synthetic generator for use while disconnected."""
        return VitalsGenerator(seed=seed)

    def _refresh(self, now: datetime):
        """Pull the last ``days_back`` days of metrics after (re)connect."""
        end = now.date()
        start = end - timedelta(days=self._days_back)
        env = self.fetch_daily_metrics(start.isoformat(), end.isoformat())
        self._records = env["records"]
        self._sleep_records = self.fetch_sleep_sessions(
            start.isoformat(), end.isoformat())["records"]
        self._latest_vitals = (self.to_hub_vitals(self._records[-1])
                               if self._records else None)

    @property
    def latest_vitals(self):
        """Hub vitals dict from the newest real record, or None when absent."""
        return self._latest_vitals

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # shared module contract
    # ------------------------------------------------------------------
    def tick(self, now: datetime, minute: int) -> list:
        """Check connection on first tick, then at most every check interval.

        Emits ``healthkit.connected`` / ``healthkit.disconnected`` events only
        on state transitions so the event log stays quiet while steady-state.
        """
        if (self._last_check_minute is not None and
                minute - self._last_check_minute < self._check_interval_min):
            return []
        self._last_check_minute = minute
        status = self.check()
        connected = status.get("connected", False)
        if connected == self._connected:
            return []
        self._connected = connected
        if connected:
            self._refresh(now)
            return [{
                "type": "healthkit.connected",
                "level": "info",
                "detail": {"categories": status["categories"],
                           "metrics_days": len(self._records)},
            }]
        self._records, self._sleep_records, self._latest_vitals = [], [], None
        return [{
            "type": "healthkit.disconnected",
            "level": "nudge",
            "detail": {"error": status.get("error", "no health records")},
        }]

    def handle_event(self, event, minute, now, outbox, emit, ctx) -> None:
        """Outbox side-effects for bridge transitions (no-ops otherwise)."""
        etype = event.get("type") if isinstance(event, dict) else None
        user = (ctx or {}).get("user_name") or "there"
        if etype == "healthkit.connected":
            outbox.send(
                "info",
                "Muse HealthMesh: Apple Watch connected — live health data "
                "is now flowing into the hub.")
        elif etype == "healthkit.disconnected":
            outbox.send(
                "info",
                "Muse HealthMesh: Apple Health is disconnected — using "
                "synthetic vitals until it reconnects.")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def _print_check(bridge: HealthKitBridge) -> int:
    status = bridge.check()
    print("Muse HealthMesh — Apple Health bridge")
    print("Connected: %s" % ("yes" if status["connected"] else "no"))
    if status["categories"]:
        for name, count in sorted(status["categories"].items()):
            print("  %s: %d records" % (name, count))
    else:
        print("  no category data")
    if status.get("error"):
        print("Note: %s" % status["error"])
    if not status["connected"]:
        print("Using synthetic vitals until Apple Health is connected. "
              "See APPLE.md for setup steps.")
    return 0


def _print_metrics(bridge: HealthKitBridge, start: str, end: str) -> int:
    env = bridge.fetch_daily_metrics(start, end)
    if not env["records"]:
        print("no records")
        cov = env.get("coverage") or {}
        if cov.get("warning"):
            print("Coverage warning: %s" % cov["warning"])
        if env.get("error"):
            print("Error: %s" % env["error"])
        return 0
    print("date        rest_hr  hrv_ms  steps   sleep_h  kcal")
    for rec in env["records"]:
        print("%-11s  %-8s %-7s %-7s %-7s %s" % (
            rec["date"], rec["resting_hr"], rec["hrv_ms"], rec["steps"],
            rec["sleep_hours"], rec["active_kcal"]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Muse HealthMesh Apple Health bridge (health-cli).")
    parser.add_argument("--cli-path", default=DEFAULT_CLI)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true",
                       help="print connection status and per-category counts")
    group.add_argument("--metrics", nargs=2, metavar=("START", "END"),
                       help="print normalized daily metrics for START..END "
                            "(YYYY-MM-DD)")
    args = parser.parse_args(argv)
    bridge = HealthKitBridge(cli_path=args.cli_path, timeout=args.timeout)
    if args.check:
        return _print_check(bridge)
    return _print_metrics(bridge, args.metrics[0], args.metrics[1])


if __name__ == "__main__":
    sys.exit(main())
