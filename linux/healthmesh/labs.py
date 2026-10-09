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

"""Biomarker tracking and doctor visit summary for Muse HealthMesh.

``LabTracker`` stores lab entries (JSON list, one per ``add_entry``) and
flags values outside their reference range or sharp >20% moves. The hub
calls ``doctor_summary_html`` at the end of a run to render a clean,
one-page summary the user can bring to a visit.

All wording is suggestion-framing: markers show "outside reference
range", never diagnoses. No personal data is hardcoded; sample entries
are seeded by the caller (tests) only.
"""

from __future__ import annotations

import html
import json
import os
from datetime import datetime, timezone

_MOVE_PCT = 0.05   # trend: last-two change big enough to call a move
_SHARP_PCT = 0.20  # flag: change big enough to call a "sharp" move

DOCTOR_QUESTIONS = (
    "Are there any biomarkers here I should keep watching, and how often "
    "should they be re-tested?",
    "Could any of my current medications or supplements be affecting these results?",
    "Are there lifestyle changes (diet, sleep, activity) that would help the "
    "flagged markers move back toward range?",
)

_TREND_ARROW = {"up": "\u2191", "down": "\u2193", "stable": "\u2192",
                "insufficient": "\u2013"}


class LabTracker:
    def __init__(self, path="labs.json"):
        self._path = path
        self._entries = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                try:
                    self._entries = json.load(fh) or []
                except ValueError:
                    self._entries = []

    # -- data ---------------------------------------------------------------

    def _save(self):
        os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(self._entries, fh, ensure_ascii=False, indent=2)

    def add_entry(self, name, value, unit, date, ref_low, ref_high) -> dict:
        entry = {"name": name, "value": value, "unit": unit, "date": date,
                 "ref_low": ref_low, "ref_high": ref_high}
        self._entries.append(entry)
        self._save()
        return entry

    def _by_name(self, name) -> list:
        return [e for e in self._entries if e.get("name") == name]

    def latest(self, name):
        entries = self._by_name(name)
        return entries[-1] if entries else None

    # -- analysis -----------------------------------------------------------

    def trend(self, name) -> str:
        """'up'|'down'|'stable'|'insufficient' from the last two values."""
        entries = self._by_name(name)
        if len(entries) < 2:
            return "insufficient"
        prev, last = entries[-2]["value"], entries[-1]["value"]
        if prev == 0:
            return "insufficient"
        change = (last - prev) / abs(prev)
        if abs(change) <= _MOVE_PCT:
            return "stable"
        return "up" if change > 0 else "down"

    def flags(self) -> list:
        """Flags for latest values: out-of-range, or >20% move vs previous."""
        out = []
        names = []
        for entry in self._entries:
            if entry.get("name") not in names:
                names.append(entry["name"])
        for name in names:
            entries = self._by_name(name)
            last = entries[-1]
            value, lo, hi = last["value"], last.get("ref_low"), last.get("ref_high")
            if lo is not None and value < lo:
                out.append({"name": name, "issue": "below range"})
            elif hi is not None and value > hi:
                out.append({"name": name, "issue": "above range"})
            if len(entries) >= 2 and entries[-2]["value"] != 0:
                change = (value - entries[-2]["value"]) / abs(entries[-2]["value"])
                if abs(change) > _SHARP_PCT:
                    out.append({"name": name,
                                "issue": "sharp rise" if change > 0 else "sharp drop"})
        return out

    # -- doctor summary -----------------------------------------------------

    def doctor_summary_html(self, path, user_name="Himangi", meds=()) -> str:
        """Write a clean one-page visit summary; return ``path``."""
        flagged = {f["name"] for f in self.flags()}
        names = []
        for entry in self._entries:
            if entry.get("name") not in names:
                names.append(entry["name"])

        rows = []
        for name in names:
            entry = self.latest(name)
            lo, hi = entry.get("ref_low"), entry.get("ref_high")
            in_range = (lo is None or entry["value"] >= lo) and \
                       (hi is None or entry["value"] <= hi)
            marker = "\u2713" if in_range else "\u26a0"  # ✓ / ⚠
            tr = self.trend(name)
            rows.append(
                "<tr><td>{marker}</td><td>{name}</td><td>{value} {unit}</td>"
                "<td>{lo}\u2013{hi}</td><td>{arrow} {trend}</td><td>{date}</td></tr>"
                .format(marker=marker, name=html.escape(str(name)),
                        value=html.escape(str(entry["value"])),
                        unit=html.escape(str(entry.get("unit", ""))),
                        lo=html.escape(str(lo if lo is not None else "\u2013")),
                        hi=html.escape(str(hi if hi is not None else "\u2013")),
                        arrow=_TREND_ARROW[tr], trend=tr,
                        date=html.escape(str(entry.get("date", "")))))
        rows_html = "\n".join(rows) if rows else \
            "<tr><td colspan=\"6\">No biomarker entries yet.</td></tr>"

        flag_lines = ""
        if flagged:
            items = "".join("<li>{name}: {issue}</li>".format(
                name=html.escape(f["name"]), issue=html.escape(f["issue"]))
                for f in self.flags())
            flag_lines = ("<h2>Flags to discuss</h2>"
                          "<p>These latest values are outside their reference "
                          "range or moved sharply since the previous result. "
                          "They are worth mentioning to your doctor; they are "
                          "not a diagnosis.</p><ul>{items}</ul>").format(items=items)

        meds_html = ""
        if meds:
            meds_html = "<h2>Current medications</h2><ul>" + "".join(
                "<li>{m}</li>".format(m=html.escape(str(m))) for m in meds) + "</ul>"

        questions = "".join("<li>{q}</li>".format(q=html.escape(q))
                            for q in DOCTOR_QUESTIONS)

        today = datetime.now(timezone.utc).date().isoformat()
        doc = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8">
<title>Muse HealthMesh &mdash; visit summary</title>
<style>
body {{ font-family: sans-serif; max-width: 720px; margin: 2em auto;
       padding: 0 1em; color: #222; }}
table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.5em; text-align: left; }}
th {{ background: #f5f5f5; }}
h1 {{ font-size: 1.4em; }} h2 {{ font-size: 1.15em; margin-top: 1.5em; }}
.meta {{ color: #666; }}
</style>
</head>
<body>
<h1>Muse HealthMesh &mdash; visit summary</h1>
<p class="meta">Prepared for {user} &middot; {today}</p>
<h2>Latest biomarkers</h2>
<table>
<tr><th></th><th>Biomarker</th><th>Latest</th><th>Reference range</th>
<th>Trend</th><th>Date</th></tr>
{rows}
</table>
{flags}
{meds}
<h2>Questions for your doctor</h2>
<ul>
{questions}
</ul>
</body>
</html>
""".format(user=html.escape(user_name), today=today, rows=rows_html,
           flags=flag_lines, meds=meds_html, questions=questions)

        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc)
        return path
