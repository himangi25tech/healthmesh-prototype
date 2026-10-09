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

"""Simulated Android TV overlay node for Muse HealthMesh.

REAL PATH (on hardware): the Pi hub drives the household Android TV either
via the Android TV Remote2 protocol (the protocol used by the Google TV /
Android TV phone remote app) or through the Home Assistant ``androidtv``
integration over ADB, pushing an overlay/toast for alerts and a digest card
for morning summaries.

PROTOTYPE (here): there is no TV, so ``TvNode`` only logs what WOULD be
shown. Each call appends one JSON line to ``tv_display.log`` and mirrors it
through the outbox with kind ``tv_display`` - the log line is the contract a
real TV driver would implement.
"""

from __future__ import annotations

import json

from .outbox import Outbox

KINDS = ("alert", "digest")


class TvNode:
    def __init__(self, outbox: Outbox, log_path):
        self._outbox = outbox
        self._log_path = log_path

    def _show(self, kind: str, text: str, now) -> dict:
        if kind not in KINDS:
            raise ValueError("kind must be one of %s, got %r" % (KINDS, kind))
        entry = {"ts": now.isoformat(), "kind": kind, "text": text}
        with open(self._log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._outbox.send("tv_display", "TV overlay: " + text)
        return entry

    def display_alert(self, text: str, now) -> dict:
        """Show an urgent overlay on the TV; return the logged dict."""
        return self._show("alert", text, now)

    def display_digest(self, text: str, now) -> dict:
        """Show a low-key digest card on the TV; return the logged dict."""
        return self._show("digest", text, now)

    def tick(self, now, minute) -> list:
        """The TV node is purely reactive; no periodic events."""
        return []

    def handle_event(self, event, minute, now, outbox, emit, ctx) -> None:
        """Reserved for future TV reactions (e.g. bedtime dimming)."""
        return None
