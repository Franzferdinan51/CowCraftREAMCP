"""Walk, loot, and accept quests through a CowCraft client.

path_preview rejects destinations farther than 40 yards. goto clamps each
leg to 35 yards, walks the returned waypoints, and stops within 4 yards.
"""

from __future__ import annotations

from cowcraft_world import (
    clamp_step,
    horiz,
    is_lootable,
    is_stuck,
    player_xyz,
    quest_offer,
    self_body,
)

PATH_CLAMP_YARDS = 35.0
ARRIVE_YARDS = 4.0
MAX_LEGS = 15
STEP_YARDS = 5.0
INTERACT_YARDS = 5.0


def _waypoint(point, fallback_z):
    if isinstance(point, dict):
        return float(point["x"]), float(point["y"]), float(point.get("z", fallback_z))
    return float(point[0]), float(point[1]), float(point[2] if len(point) > 2 else fallback_z)


class Nav:
    """One character's movement and corpse/quest actions."""

    def __init__(self, client):
        self.client = client
        seeded = getattr(client, "state", None)
        self.state = seeded if isinstance(seeded, dict) else {}

    def refresh(self, **observe_kwargs):
        self.state = self.client.observe(**observe_kwargs) or {}
        return self.state

    def position(self):
        return player_xyz(self.state)

    def _xyz(self):
        x, y, z = self.position()
        if x is None or y is None or z is None:
            self.refresh()
            x, y, z = self.position()
        return x, y, z

    def goto(self, tx, ty, tz):
        """Walk to a point in legs of at most 35 yards. At most 15 legs."""
        tx, ty, tz = float(tx), float(ty), float(tz)
        for _ in range(MAX_LEGS):
            x, y, z = self._xyz()
            if x is None:
                return {"ok": False, "reason": "no_position"}
            if horiz(x, y, tx, ty) <= ARRIVE_YARDS:
                return {"ok": True, "reason": "arrived"}
            px, py, pz = clamp_step(x, y, z, tx, ty, tz, PATH_CLAMP_YARDS)
            preview = self.client.action("path_preview", {"x": px, "y": py, "z": pz}) or {}
            waypoints = preview.get("waypoints") or []
            if not waypoints:
                status = preview.get("path_status") or "no_path"
                return {"ok": False, "reason": status, "preview": preview}
            for point in waypoints:
                wx, wy, wz = _waypoint(point, pz)
                self.client.action("move", {"x": wx, "y": wy, "z": wz})
            self.refresh()
            if is_stuck(self_body(self.state)):
                return {"ok": False, "reason": "stuck"}
        return {"ok": False, "reason": "max_legs"}

    def step_toward(self, tx, ty, tz):
        """One move of at most 5 yards. Used while following."""
        x, y, z = self._xyz()
        if x is None:
            return {"ok": False, "reason": "no_position"}
        px, py, pz = clamp_step(x, y, z, float(tx), float(ty), float(tz), STEP_YARDS)
        self.client.action("move", {"x": px, "y": py, "z": pz})
        return {"ok": True, "reason": "step"}

    def loot_corpse(self, unit):
        """loot_open, loot_take each slot, loot_money, loot_close."""
        if not is_lootable(unit):
            return {"looted": False, "reason": "not_eligible"}
        guid = str(unit.get("guid"))
        opened = self.client.action("loot_open", {"guid": guid}) or {}
        slots = []
        items = opened.get("items") or opened.get("loot") or []
        if isinstance(items, dict):
            items = items.get("items") or []
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("slot"), int):
                slots.append(item["slot"])
        has_money = bool(opened.get("gold") or opened.get("money") or opened.get("copper"))
        if has_money:
            self.client.action("loot_money", {})
        for slot in slots:
            self.client.action("loot_take", {"slot": slot})
        self.client.action("loot_close", {"guid": guid})
        return {"looted": True, "slots": slots, "money": has_money}

    def accept_quest(self, giver):
        """Walk within 5 yards, interact, then quest_accept the open offer."""
        if not isinstance(giver, dict):
            return {"accepted": False, "reason": "no_giver"}
        guid = str(giver.get("guid") or "")
        gx, gy = giver.get("x"), giver.get("y")
        gz = giver.get("z", 0)
        x, y, _z = self._xyz()
        if x is not None and gx is not None and gy is not None:
            if horiz(x, y, gx, gy) >= INTERACT_YARDS:
                walked = self.goto(gx, gy, gz)
                if not walked.get("ok"):
                    return {"accepted": False, "reason": walked.get("reason")}
        self.client.action("interact", {"guid": guid})
        self.refresh()
        offer = quest_offer(self.state)
        if not offer or not offer.get("can_accept"):
            return {"accepted": False, "reason": "no_offer"}
        quest_id = int(offer["quest_id"])
        giver_guid = str(offer.get("giver_guid") or guid)
        self.client.action("quest_accept", {"guid": giver_guid, "quest_id": quest_id})
        return {"accepted": True, "quest_id": quest_id, "guid": giver_guid}

    def sell(self, vendor_guid, item_guid, count):
        return self.client.action("vendor_sell", {
            "guid": str(vendor_guid),
            "item_guid": str(item_guid),
            "count": int(count),
        })

    def inventory(self):
        self.refresh(sections=["inventory"])
        items = self.state.get("inventory")
        return items if isinstance(items, list) else []
