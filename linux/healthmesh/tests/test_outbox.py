# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.outbox: JSONL schema, kinds, console echo."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone

from healthmesh.outbox import Outbox, KINDS, SESSION_ID


class TestOutbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "outbox.jsonl")
        self.fixed_ts = datetime(2026, 10, 8, 3, 16, tzinfo=timezone.utc)

    def make(self, **kw):
        kw.setdefault("clock", lambda: self.fixed_ts)
        return Outbox(self.path, **kw)

    def read_lines(self):
        with open(self.path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_schema_fields(self):
        box = self.make()
        box.send("alert", "Are you okay?")
        box.close()
        (line,) = self.read_lines()
        self.assertEqual(line["ts"], "2026-10-08T03:16:00+00:00")
        self.assertEqual(line["session_id"], SESSION_ID)
        self.assertEqual(line["session_id"], "healthmesh-demo")
        self.assertEqual(line["kind"], "alert")
        self.assertEqual(line["text"], "Are you okay?")

    def test_all_kinds_valid(self):
        box = self.make()
        for kind in ("info", "reminder", "alert", "emergency",
                     "grocery_order", "tv_display"):
            box.send(kind, "x")
        box.close()
        self.assertEqual([l["kind"] for l in self.read_lines()],
                         ["info", "reminder", "alert", "emergency",
                          "grocery_order", "tv_display"])
        self.assertEqual(set(KINDS), {"info", "reminder", "alert", "emergency",
                                      "grocery_order", "tv_display"})

    def test_invalid_kind_rejected(self):
        box = self.make()
        with self.assertRaises(ValueError):
            box.send("page", "nope")
        box.close()
        self.assertEqual(self.read_lines(), [])

    def test_appends_one_json_per_line(self):
        box = self.make()
        box.send("info", "first")
        box.send("emergency", "second")
        box.close()
        with open(self.path, encoding="utf-8") as fh:
            raw = fh.read().splitlines()
        self.assertEqual(len(raw), 2)
        self.assertEqual([json.loads(l)["text"] for l in raw],
                         ["first", "second"])

    def test_console_echo(self):
        box = self.make()
        buf = io.StringIO()
        with redirect_stdout(buf):
            box.send("reminder", "take your pill")
        box.close()
        echoed = json.loads(buf.getvalue().strip())
        self.assertEqual(echoed["kind"], "reminder")
        self.assertEqual(echoed["text"], "take your pill")


if __name__ == "__main__":
    unittest.main()
