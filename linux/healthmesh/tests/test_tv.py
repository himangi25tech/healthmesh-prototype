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

"""Tests for healthmesh.tv: TvNode log lines and outbox mirroring."""

import json
import os
import tempfile
import unittest
from datetime import datetime

from healthmesh.outbox import Outbox
from healthmesh.tv import TvNode

NOW = datetime(2026, 10, 7, 23, 5)


class TestTv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "tv_display.log")
        self.box_path = os.path.join(self.tmp, "outbox.jsonl")
        self.box = Outbox(self.box_path, clock=lambda: NOW)
        self.tv = TvNode(self.box, self.log)

    def tearDown(self):
        self.box.close()

    def read_log(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def read_outbox(self):
        with open(self.box_path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_display_alert_logs_and_mirrors(self):
        entry = self.tv.display_alert("High heart rate detected", NOW)
        self.assertEqual(entry["ts"], NOW.isoformat())
        self.assertEqual(entry["kind"], "alert")
        self.assertEqual(entry["text"], "High heart rate detected")
        self.assertEqual(self.read_log(), [entry])

        (line,) = self.read_outbox()
        self.assertEqual(line["kind"], "tv_display")
        self.assertEqual(line["text"],
                         "TV overlay: High heart rate detected")

    def test_display_digest_logs_and_mirrors(self):
        entry = self.tv.display_digest("Morning summary ready", NOW)
        self.assertEqual(entry["kind"], "digest")
        self.assertEqual(self.read_log(), [entry])

        (line,) = self.read_outbox()
        self.assertEqual(line["kind"], "tv_display")
        self.assertTrue(line["text"].startswith("TV overlay:"))
        self.assertEqual(line["text"], "TV overlay: Morning summary ready")

    def test_appends_across_calls(self):
        self.tv.display_alert("one", NOW)
        self.tv.display_digest("two", NOW)
        log = self.read_log()
        self.assertEqual([e["kind"] for e in log], ["alert", "digest"])
        outbox = self.read_outbox()
        self.assertEqual([l["kind"] for l in outbox],
                         ["tv_display", "tv_display"])
        self.assertEqual([l["text"] for l in outbox],
                         ["TV overlay: one", "TV overlay: two"])

    def test_invalid_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.tv._show("page", "nope", NOW)

    def test_contract_hooks_exist(self):
        self.assertEqual(self.tv.tick(NOW, 3), [])
        self.assertIsNone(self.tv.handle_event({}, 3, NOW, self.box,
                                               lambda *a: None, {}))


if __name__ == "__main__":
    unittest.main()
