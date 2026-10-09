# Copyright (c) Meta Platforms, Inc. and affiliates.
"""Tests for healthmesh.kitchen: scans, low-stock, grocery loop, interaction."""

import unittest
from datetime import datetime

from healthmesh.kitchen import Fridge, INTERACTIONS
from healthmesh.meds import MEDS


class _OutboxStub:
    def __init__(self):
        self.sent = []

    def send(self, kind, text):
        self.sent.append((kind, text))
        return {"kind": kind, "text": text}


class _EmitStub:
    def __init__(self):
        self.events = []

    def __call__(self, etype, level, detail, now):
        self.events.append(
            {"type": etype, "level": level, "detail": detail})


BASE = datetime(2026, 10, 8, 6, 0)


def ctx_with(*meds):
    return {"vitals": {"hr": 70, "hrv_ms": 60, "spo2": 98,
                       "bp_sys": 120, "bp_dia": 80},
            "meds": {m: "due" for m in meds},
            "contact": "Aarav (brother)", "user_name": "Himangi"}


class TestScanMath(unittest.TestCase):
    def test_consumption_delta(self):
        f = Fridge()
        # -0.6 leaves 0.4 L, still above low_at 0.3: a scan event only.
        (ev,) = f.scan({"milk": -0.6}, BASE)
        self.assertEqual(ev["type"], "kitchen.scan")
        self.assertEqual(ev["level"], "info")
        self.assertAlmostEqual(f.inventory()["milk"]["qty"], 0.4)
        self.assertEqual(ev["detail"]["changes"], {"milk": -0.6})

    def test_clamp_at_zero(self):
        f = Fridge()
        f.scan({"oats": -400}, BASE)
        f.scan({"oats": -400}, BASE)
        self.assertEqual(f.inventory()["oats"]["qty"], 0.0)

    def test_restock_and_unknown_item(self):
        f = Fridge()
        f.scan({"milk": -1.0}, BASE)
        f.scan({"milk": 2.0}, BASE)
        self.assertAlmostEqual(f.inventory()["milk"]["qty"], 2.0)
        f.scan({"grapefruit": 2}, BASE)
        self.assertEqual(f.inventory()["grapefruit"]["qty"], 2.0)


class TestLowStock(unittest.TestCase):
    def test_fires_once_per_run(self):
        scans = [(30, {"milk": -0.8})]
        f = Fridge(scripted_scans=scans)
        all_events = []
        for minute in range(60):
            all_events.extend(f.tick(BASE, minute))
        lows = [e for e in all_events if e["type"] == "kitchen.low_stock"]
        self.assertEqual(len(lows), 1)
        self.assertEqual(lows[0]["level"], "nudge")
        self.assertEqual(lows[0]["detail"]["item"], "milk")
        self.assertAlmostEqual(lows[0]["detail"]["qty"], 0.2)
        self.assertEqual(lows[0]["detail"]["unit"], "L")

    def test_not_fired_above_threshold(self):
        f = Fridge(scripted_scans=[(5, {"milk": -0.1})])
        events = [e for minute in range(10) for e in f.tick(BASE, minute)]
        self.assertFalse([e for e in events
                          if e["type"] == "kitchen.low_stock"])

    def test_no_flag_without_scan(self):
        f = Fridge()
        self.assertEqual(f.tick(BASE, 0), [])

    def test_handle_event_sends_reminder(self):
        f = Fridge()
        outbox = _OutboxStub()
        f.handle_event({"type": "kitchen.low_stock", "level": "nudge",
                        "detail": {"item": "milk", "qty": 0.2, "unit": "L"}},
                       30, BASE, outbox, _EmitStub(), ctx_with())
        self.assertEqual(len(outbox.sent), 1)
        kind, text = outbox.sent[0]
        self.assertEqual(kind, "reminder")
        self.assertIn("milk", text)
        self.assertIn("0.2 L", text)
        self.assertIn("Muse kitchen", text)


class TestGroceryLoop(unittest.TestCase):
    def test_grocery_list_contents(self):
        f = Fridge()
        f.scan({"milk": -0.8, "oats": -400}, BASE)
        lst = f.grocery_list()
        by_item = {e["item"]: e for e in lst}
        self.assertEqual(set(by_item), {"milk", "oats"})
        self.assertEqual(by_item["milk"]["unit"], "L")
        self.assertAlmostEqual(by_item["milk"]["qty"], 0.2)
        self.assertEqual(by_item["oats"]["unit"], "g")
        for entry in lst:
            self.assertIn("note", entry)

    def test_order_draft_text_and_notes(self):
        f = Fridge()
        f.scan({"milk": -0.8, "oats": -400}, BASE)
        order = f.build_order(BASE)
        self.assertEqual(order["total_items"], 2)
        self.assertEqual(len(order["items"]), 2)
        self.assertTrue(any("morning pill" in n for n in order["notes"]))
        self.assertTrue(any("Evening vitamin" in n for n in order["notes"]))

    def test_finalize_sends_order_and_emits(self):
        f = Fridge()
        f.scan({"milk": -0.8, "oats": -400}, BASE)
        outbox, emit = _OutboxStub(), _EmitStub()
        f.finalize(BASE, outbox, emit)
        self.assertEqual(len(outbox.sent), 1)
        kind, text = outbox.sent[0]
        self.assertEqual(kind, "grocery_order")
        self.assertIn("milk", text)
        self.assertIn("oats", text)
        self.assertIn("morning pill with food", text)
        self.assertIn("order it", text)
        self.assertEqual(len(emit.events), 1)
        self.assertEqual(emit.events[0]["type"], "kitchen.order")
        self.assertEqual(emit.events[0]["level"], "info")

    def test_finalize_empty_list_sends_nothing(self):
        f = Fridge()
        outbox, emit = _OutboxStub(), _EmitStub()
        f.finalize(BASE, outbox, emit)
        self.assertEqual(outbox.sent, [])
        self.assertEqual(emit.events, [])


class TestInteraction(unittest.TestCase):
    def test_fires_when_med_present(self):
        f = Fridge(meds={"morning_bp_pill"})
        events = f.scan({"grapefruit": 2}, BASE)
        hits = [e for e in events if e["type"] == "kitchen.interaction"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["level"], "alert")
        self.assertEqual(hits[0]["detail"],
                         {"food": "grapefruit", "med": "morning_bp_pill"})

    def test_silent_when_med_absent(self):
        f = Fridge(meds={"evening_vitamin"})
        events = f.scan({"grapefruit": 2}, BASE)
        self.assertFalse([e for e in events
                          if e["type"] == "kitchen.interaction"])

    def test_silent_on_removal(self):
        f = Fridge(meds={"morning_bp_pill"})
        events = f.scan({"grapefruit": -2}, BASE)
        self.assertFalse([e for e in events
                          if e["type"] == "kitchen.interaction"])

    def test_default_meds_cover_all_schedule(self):
        f = Fridge()
        self.assertEqual(f._meds, set(MEDS))

    def test_handle_event_alert_text(self):
        f = Fridge()
        outbox = _OutboxStub()
        ev = {"type": "kitchen.interaction", "level": "alert",
              "detail": {"food": "grapefruit", "med": "morning_bp_pill"}}
        f.handle_event(ev, 5, BASE, outbox, _EmitStub(),
                       ctx_with("morning_bp_pill", "evening_vitamin"))
        self.assertEqual(len(outbox.sent), 1)
        kind, text = outbox.sent[0]
        self.assertEqual(kind, "alert")
        self.assertIn("grapefruit", text)
        self.assertIn("morning BP pill", text)
        self.assertIn("doctor or pharmacist", text)

    def test_handle_event_skips_alert_when_med_not_in_ctx(self):
        f = Fridge()
        outbox = _OutboxStub()
        ev = {"type": "kitchen.interaction", "level": "alert",
              "detail": {"food": "grapefruit", "med": "morning_bp_pill"}}
        f.handle_event(ev, 5, BASE, outbox, _EmitStub(),
                       ctx_with("evening_vitamin"))
        self.assertEqual(outbox.sent, [])

    def test_interactions_contract(self):
        self.assertEqual(INTERACTIONS, {"morning_bp_pill": ["grapefruit"]})


class TestDeterminism(unittest.TestCase):
    SCANS = [(10, {"milk": -0.8}), (20, {"oats": -400, "grapefruit": 2}),
             (40, {"eggs": -5})]

    def _run(self, seed):
        f = Fridge(seed=seed, scripted_scans=list(self.SCANS))
        events = [e for m in range(60) for e in f.tick(BASE, m)]
        outbox, emit = _OutboxStub(), _EmitStub()
        for e in events:
            f.handle_event(e, 0, BASE, outbox, emit, ctx_with(*MEDS))
        f.finalize(BASE, outbox, emit)
        return events, outbox.sent, emit.events, f.build_order(BASE)

    def test_same_seed_identical(self):
        a = self._run(7)
        b = self._run(7)
        self.assertEqual(a, b)

    def test_different_seed_identical_scripted_run(self):
        # Scans are scripted and math is exact, so seed changes nothing.
        self.assertEqual(self._run(7), self._run(12345))


if __name__ == "__main__":
    unittest.main()
