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

"""Smart fridge + grocery loop for the Muse HealthMesh hub prototype.

Simulates a camera-equipped smart fridge: an inventory of staple items,
scripted re-scans that apply consumption deltas, low-stock detection that
feeds the grocery list, and a med-aware order draft.

One ``tick(now, minute_index)`` == one simulated minute. Scripted scans are
given as ``(minute_offset, changes_dict)`` pairs, e.g.
``(30, {"milk": -0.8, "oats": -400})``. All scan math is exact and
deterministic; a seeded ``random.Random`` is kept for API symmetry with the
other modules (same seed -> byte-identical events and order drafts).

Events emitted:
  kitchen.scan        (info)  - a scan was applied
  kitchen.low_stock   (nudge) - an item newly dropped below its low_at level
  kitchen.interaction (alert) - a scanned item interacts with a scheduled med
  kitchen.order       (info)  - the end-of-run grocery order draft
"""

from __future__ import annotations

import random
from datetime import datetime

from .meds import MEDS

# Med -> foods that may interact with it. The kitchen only *flags* the
# combination; it never makes a medical claim (see handle_event).
INTERACTIONS = {
    "morning_bp_pill": ["grapefruit"],
}

# item -> starting {"qty", "unit", "low_at"}. An item flags kitchen.low_stock
# the first time its quantity drops strictly below low_at.
STARTER_STOCK = {
    "milk": {"qty": 1.0, "unit": "L", "low_at": 0.3},
    "oats": {"qty": 500.0, "unit": "g", "low_at": 150.0},
    "eggs": {"qty": 6.0, "unit": "count", "low_at": 2.0},
    "bananas": {"qty": 4.0, "unit": "count", "low_at": 1.0},
}

# Med-aware notes added to the order draft when the paired staple runs low.
ORDER_NOTES = (
    ("oats", "Take your morning pill with food — oats are running low"),
    ("milk", "Evening vitamin absorbs better with a little food — milk is low"),
)

REMINDER_TEXT = ("Muse kitchen: {item} is running low ({qty} {unit} left). "
                 "Added to your grocery list.")
INTERACTION_TEXT = (
    "Heads up: grapefruit can interact with your morning BP pill — "
    "check with your doctor or pharmacist before having it.")
ORDER_TEXT = ("Muse grocery order draft ({n} items): {items}. {notes} "
              "Say 'order it' to place via your shopping skill.")


def _fmt(qty: float) -> str:
    """Compact number formatting: 2.0 -> '2', 0.2 -> '0.2'."""
    return "%g" % qty


class Fridge:
    """Seeded smart-fridge simulation + grocery loop."""

    def __init__(self, seed: int = 7, scripted_scans=None, meds=None):
        self._rng = random.Random(seed)
        self._inventory = {
            item: dict(spec) for item, spec in STARTER_STOCK.items()
        }
        # (minute_offset, changes) sorted by offset; consumed by tick().
        self._scans = sorted(scripted_scans or [], key=lambda s: s[0])
        # Names of meds on the user's schedule; interactions only fire for
        # foods paired with a med that is actually scheduled.
        self._meds = set(meds) if meds is not None else set(MEDS)
        # Items already flagged kitchen.low_stock this run (each flags once).
        self._flagged = set()

    # -- inspection ----------------------------------------------------

    def inventory(self) -> dict:
        """Snapshot of current inventory (copies, safe to keep)."""
        return {item: dict(spec) for item, spec in self._inventory.items()}

    def low_items(self) -> list:
        """Names of items currently below their low_at level, in stock order."""
        return [item for item, spec in self._inventory.items()
                if spec["qty"] < spec["low_at"]]

    # -- scanning ------------------------------------------------------

    def scan(self, changes: dict, now: datetime) -> list:
        """Apply consumption/restock deltas (clamped at 0); return events."""
        applied = {}
        added = []
        for item, delta in changes.items():
            spec = self._inventory.get(item)
            if spec is None:
                spec = {"qty": 0.0, "unit": "count", "low_at": 0.0}
                self._inventory[item] = spec
            spec["qty"] = max(0.0, spec["qty"] + delta)
            applied[item] = delta
            if delta > 0 and spec["qty"] > 0:
                added.append(item)

        events = [{
            "type": "kitchen.scan",
            "level": "info",
            "detail": {"changes": dict(applied),
                       "inventory": self.inventory()},
        }]

        # Low-stock: each item flags once per run, the first time it dips.
        for item in self.low_items():
            if item in self._flagged:
                continue
            self._flagged.add(item)
            spec = self._inventory[item]
            events.append({
                "type": "kitchen.low_stock",
                "level": "nudge",
                "detail": {"item": item, "qty": spec["qty"],
                           "unit": spec["unit"]},
            })

        # Interaction flags: only for foods ADDED while their med is on the
        # user's schedule.
        for item in added:
            for med, foods in INTERACTIONS.items():
                if item in foods and med in self._meds:
                    events.append({
                        "type": "kitchen.interaction",
                        "level": "alert",
                        "detail": {"food": item, "med": med},
                    })
        return events

    def tick(self, now: datetime, minute: int) -> list:
        """Advance one simulated minute; fire any due scripted scans."""
        events = []
        while self._scans and self._scans[0][0] <= minute:
            _, changes = self._scans.pop(0)
            events.extend(self.scan(changes, now))
        return events

    # -- grocery loop --------------------------------------------------

    def grocery_list(self) -> list:
        """Order-ready list of everything currently below its low level."""
        out = []
        for item in self.low_items():
            spec = self._inventory[item]
            out.append({
                "item": item,
                "qty": spec["qty"],
                "unit": spec["unit"],
                "note": "low stock",
            })
        return out

    def build_order(self, now: datetime) -> dict:
        """Draft order: items plus med-aware notes for paired staples."""
        items = self.grocery_list()
        low = {entry["item"] for entry in items}
        notes = [note for item, note in ORDER_NOTES if item in low]
        return {"items": items, "notes": notes,
                "total_items": len(items)}

    def finalize(self, now: datetime, outbox, emit) -> None:
        """End-of-run: send the grocery order draft and emit kitchen.order."""
        order = self.build_order(now)
        if not order["total_items"]:
            return
        items_txt = ", ".join(
            "%s %s %s" % (e["item"], _fmt(e["qty"]), e["unit"])
            for e in order["items"])
        text = ORDER_TEXT.format(
            n=order["total_items"], items=items_txt,
            notes=" ".join(order["notes"]))
        outbox.send("grocery_order", text)
        emit("kitchen.order", "info",
             {"items": order["items"], "notes": order["notes"]}, now)

    # -- hub integration ------------------------------------------------

    def handle_event(self, event: dict, minute: int, now: datetime,
                     outbox, emit, ctx: dict) -> None:
        """React to kitchen events with outbox messages (side effects only)."""
        etype = event["type"]
        if etype == "kitchen.low_stock":
            d = event["detail"]
            outbox.send("reminder", REMINDER_TEXT.format(
                item=d["item"], qty=_fmt(d["qty"]), unit=d["unit"]))
        elif etype == "kitchen.interaction":
            d = event["detail"]
            if d["med"] in (ctx.get("meds") or {}):
                outbox.send("alert", INTERACTION_TEXT)
