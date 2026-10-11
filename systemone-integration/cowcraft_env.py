#!/usr/bin/env python3
"""
cowcraft_env.py — SystemOne Env backed by the Minta MCP gateway.

Implements the SystemOne `Env` protocol (observe/act) so SystemOne's
DecisionLoop (See > Decide > Act) can drive a WoW character through the
Minta gateway, which proxies to CowCraft (or a stub world).

Architecture:
    DecisionLoop --observe/act--> CowCraftEnv --MCP--> Minta gateway --> CowCraft

The judge picks from a discrete high-level action set; this env translates
each choice into concrete gateway action() calls using the latest
observation (positions, target GUIDs, etc.).

Stdlib only.
"""

from __future__ import annotations

import os
import sys
import time
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-gateway"))

try:
    from cowcraft_mcp import CowCraft, CowCraftError
except ImportError:  # pragma: no cover
    CowCraft = None
    CowCraftError = Exception


# Discrete action vocabulary the judge chooses from.
ACTIONS = [
    "move_north",
    "move_south",
    "move_east",
    "move_west",
    "attack",
    "stop",
    "interact",
    "loot",
    "wait",
]

STEP_SIZE = 6.0  # world units per move_* action


class Observation:
    """SEE: what the world looks like this tick (SystemOne loop shape)."""

    def __init__(self, text, images=(), videos=(), done=False, info=None):
        self.text = text
        self.images = images
        self.videos = videos
        self.done = done
        self.info = info or {}


class ActResult:
    """ACT: what happened after the action (SystemOne loop shape)."""

    def __init__(self, progressed, done=False, info=None):
        self.progressed = progressed
        self.done = done
        self.info = info or {}


class CowCraftEnv:
    """SystemOne Env driving a character through the Minta MCP gateway.

    Args:
        gateway_url: Minta gateway /mcp URL, e.g. http://localhost:8901/mcp
        username / password: CowCraft account credentials (env vars preferred)
        character_name: character to select (created if missing)
        char_class: WoW class id for creation (1=Warrior)
        role: free-form role label, e.g. "tank", "scout"
    """

    def __init__(
        self,
        gateway_url=None,
        username=None,
        password=None,
        character_name=None,
        char_class=1,
        role="adventurer",
        model_name="minta-agent",
    ):
        if CowCraft is None:
            raise RuntimeError("cowcraft_mcp client not importable")
        self.gateway_url = gateway_url or os.environ.get(
            "MINTA_GATEWAY_URL", "http://127.0.0.1:8901/mcp"
        )
        self.username = username or os.environ.get("MINTA_USERNAME")
        self.password = password or os.environ.get("MINTA_PASSWORD")
        if not self.username or not self.password:
            raise RuntimeError(
                "CowCraft credentials required: pass username/password or set "
                "MINTA_USERNAME / MINTA_PASSWORD"
            )
        self.character_name = character_name or self.username
        self.char_class = char_class
        self.role = role
        self.model_name = model_name

        self.game = CowCraft(url=self.gateway_url)
        self.state = {}
        self._pos = (0.0, 0.0, 0.0)
        self._hp = (1, 1)
        self._last_heartbeat = 0.0

    # -- lifecycle -------------------------------------------------------
    def connect(self):
        """Connect, login, select (or create) the character."""
        self.game.connect(self.model_name)
        self.game.login(self.username, self.password)
        chars = self.game.characters().get("characters", [])
        guid = None
        for c in chars:
            if c.get("name", "").lower() == self.character_name.lower():
                guid = c.get("guid")
                break
        if guid is None:
            created = self.game.create_character(self.character_name, self.char_class)
            guid = created.get("guid") or created.get("character", {}).get("guid")
        self.game.select_character(int(guid))
        self._refresh()
        return True

    def close(self):
        try:
            self.game.disconnect()
        except Exception:
            pass

    # -- Env protocol ----------------------------------------------------
    def observe(self):
        """SEE: fetch gateway state, render as text for the judge."""
        self._maybe_heartbeat()
        self._refresh()
        return Observation(text=self._render(), info=dict(self.state))

    def act(self, action):
        """ACT: translate a discrete action into gateway calls."""
        action = str(action).strip().lower()
        before = self._pos
        try:
            self._dispatch(action)
        except Exception as exc:  # never let one bad action kill the loop
            return ActResult(progressed=False, info={"error": str(exc)[:120]})
        time.sleep(0.6)  # let the world tick; gateway caps observe at 1/s
        self._refresh()
        moved = math.dist(self._pos[:2], before[:2])
        progressed = moved > 0.5 or action in ("attack", "interact", "loot", "stop")
        return ActResult(progressed=progressed, info={"action": action, "moved": round(moved, 2)})

    # -- internals -------------------------------------------------------
    def _maybe_heartbeat(self):
        if time.time() - self._last_heartbeat > 25:
            try:
                self.game.heartbeat()
            except Exception:
                pass
            self._last_heartbeat = time.time()

    def _refresh(self):
        try:
            self.state = self.game.observe() or {}
        except Exception:
            return
        me = self._me()
        if me:
            self._pos = (me.get("x", 0.0), me.get("y", 0.0), me.get("z", 0.0))
            self._hp = (me.get("health", 1), me.get("max_health", 1))

    def _me(self):
        for u in self.state.get("nearby_units", []):
            if u.get("name", "").lower() == self.character_name.lower():
                return u
        # fall back to first self-like entry
        units = self.state.get("nearby_units", [])
        return units[0] if units else None

    def _nearest_hostile(self):
        best, best_d = None, float("inf")
        for u in self.state.get("nearby_units", []):
            if u.get("hostile") and not u.get("dead"):
                d = math.dist((u.get("x", 0), u.get("y", 0)), self._pos[:2])
                if d < best_d:
                    best, best_d = u, d
        return best

    def _nearest_interactable(self):
        best, best_d = None, float("inf")
        for o in self.state.get("nearby_objects", []):
            d = math.dist((o.get("x", 0), o.get("y", 0)), self._pos[:2])
            if d < best_d:
                best, best_d = o, d
        return best

    def _dispatch(self, action):
        x, y, z = self._pos
        if action == "move_north":
            self.game.action("move", {"x": x, "y": y + STEP_SIZE, "z": z})
        elif action == "move_south":
            self.game.action("move", {"x": x, "y": y - STEP_SIZE, "z": z})
        elif action == "move_east":
            self.game.action("move", {"x": x + STEP_SIZE, "y": y, "z": z})
        elif action == "move_west":
            self.game.action("move", {"x": x - STEP_SIZE, "y": y, "z": z})
        elif action == "attack":
            target = self._nearest_hostile()
            if target:
                self.game.action("target", {"guid": target["guid"]})
                self.game.action("attack", {})
        elif action == "stop":
            self.game.action("stop", {})
        elif action == "interact":
            obj = self._nearest_interactable()
            if obj:
                self.game.action("interact_object", {"guid": obj["guid"]})
        elif action == "loot":
            # loot nearest corpse: open, take all, take money, close
            target = self._nearest_hostile()
            guid = target["guid"] if target and target.get("dead") else None
            if guid:
                self.game.action("loot_open", {"guid": guid})
                for slot in range(16):
                    try:
                        self.game.action("loot_take", {"slot": slot})
                    except Exception:
                        break
                try:
                    self.game.action("loot_money", {})
                except Exception:
                    pass
                self.game.action("loot_close", {})
        elif action == "wait":
            time.sleep(1.0)
        else:
            raise ValueError(f"unknown action: {action}")

    def _render(self):
        """Render gateway state as compact text for the judge."""
        x, y, z = self._pos
        hp, max_hp = self._hp
        lines = [
            f"role={self.role} name={self.character_name} "
            f"pos=({x:.1f},{y:.1f},{z:.1f}) hp={hp}/{max_hp}"
        ]
        units = self.state.get("nearby_units", [])
        if units:
            lines.append("nearby units:")
            for u in units[:8]:
                if u.get("name", "").lower() == self.character_name.lower():
                    continue
                lines.append(
                    f"  - {u.get('name')} lvl={u.get('level')} "
                    f"hostile={u.get('hostile')} dead={u.get('dead')} "
                    f"dist={math.dist((u.get('x',0),u.get('y',0)), (x,y)):.1f}"
                )
        objs = self.state.get("nearby_objects", [])
        if objs:
            lines.append(f"nearby objects: {len(objs)} (nearest: {objs[0].get('name')})")
        quests = self.state.get("quests", [])
        if quests:
            lines.append(f"quests: {len(quests)} active")
        lines.append(
            "valid actions: " + ", ".join(ACTIONS)
        )
        return "\n".join(lines)
