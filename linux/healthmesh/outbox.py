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

"""Outbox shim for the Muse HealthMesh hub prototype.

Stands in for ``musegadget send-user-msg`` (no Muse pairing exists in this
prototype). Every message is appended to ``outbox.jsonl`` as one JSON object
per line and echoed to the console:

    {"ts": "<UTC ISO-8601>", "session_id": "healthmesh-demo",
     "kind": "info|reminder|alert|emergency", "text": "<exact text sent>"}
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

SESSION_ID = "healthmesh-demo"
# Phase 2 additions: "grocery_order" (kitchen.py order drafts, stand-in for
# the shopping skill) and "tv_display" (tv.py Android TV overlay log).
KINDS = ("info", "reminder", "alert", "emergency", "grocery_order", "tv_display")


class Outbox:
    def __init__(self, path, session_id: str = SESSION_ID, clock=None):
        self._path = path
        self._session_id = session_id
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._fh = open(path, "a", encoding="utf-8")

    def send(self, kind: str, text: str) -> dict:
        if kind not in KINDS:
            raise ValueError("kind must be one of %s, got %r" % (KINDS, kind))
        line = {
            "ts": self._clock().isoformat(),
            "session_id": self._session_id,
            "kind": kind,
            "text": text,
        }
        self._fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        self._fh.flush()
        print(json.dumps(line, ensure_ascii=False), flush=True)
        return line

    def close(self) -> None:
        self._fh.close()
