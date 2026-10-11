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
    "cast",
    "stop",
    "interact",
    "loot",
    "wait",
]

STEP_SIZE = 6.0  # world units per move_* action
LOOT_SECTIONS = ("self", "nearby_units", "ui")


def learned_spells(state):
    """Spell dicts the server already says this character knows."""
    if not isinstance(state, dict):
        return []
    found = []
    for key in ("spells", "spellbook", "spell_book"):
        raw = state.get(key)
        if isinstance(raw, list):
            found.extend(raw)
        elif isinstance(raw, dict):
            for inner in ("spells", "known", "entries"):
                if isinstance(raw.get(inner), list):
                    found.extend(raw[inner])
    return [spell for spell in found if isinstance(spell, dict)]


def spell_to_cast(spells, power):
    """Pick a learned spell this power can pay for. Does not invent spell ids."""
    try:
        available = int(power)
    except (TypeError, ValueError):
        available = 0
    fallback = None
    for spell in spells or []:
        if not isinstance(spell, dict):
            continue
        sid = spell.get("spell_id", spell.get("id"))
        if isinstance(sid, bool) or not isinstance(sid, int):
            continue
        cost = spell.get("power_cost", spell.get("cost", spell.get("rage_cost", 0)))
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            cost = 0
        if available < cost:
            continue
        name = str(spell.get("name") or "").lower()
        auto = name in ("attack", "shoot")
        harmful = bool(spell.get("harmful")) or any(
            word in name for word in (
                "strike", "shot", "bolt", "fire", "frost", "shadow",
                "smite", "lightning", "wrath", "rend",
            )
        )
        if harmful and not auto:
            return sid
        if fallback is None:
            fallback = sid
    return fallback


def loot_plan(loot):
    """Slots to take, and whether the window has money. Empty when there is no window."""
    if not isinstance(loot, dict):
        return [], False
    slots = []
    for item in loot.get("items") or []:
        if not isinstance(item, dict):
            continue
        slot = item.get("slot")
        if isinstance(slot, bool) or not isinstance(slot, int):
            continue
        slots.append(slot)
    money = loot.get("gold", loot.get("money", 0))
    has_money = isinstance(money, int) and not isinstance(money, bool) and money > 0
    if isinstance(money, dict):
        has_money = any(as_int_money(money.get(key)) > 0 for key in ("gold", "silver", "copper"))
    return slots, has_money


def as_int_money(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def loot_window(state):
    """Loot dict from an observe, including a ui section. Empty when closed."""
    if not isinstance(state, dict):
        return {}
    for key in ("ordinary_ui", "ui", "combat_ui"):
        block = state.get(key)
        if isinstance(block, dict) and isinstance(block.get("loot"), dict):
            return block["loot"]
    loot = state.get("loot")
    if isinstance(loot, dict):
        return loot
    return {}


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
        token_path=None,
        model_path=None,
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

        self.game = CowCraft(
            url=self.gateway_url,
            token_path=token_path,
            model_path=model_path,
        )
        self.state = {}
        self.spells = []
        self._pos = (0.0, 0.0, 0.0)
        self._hp = (1, 1)
        self._last_heartbeat = 0.0

    # -- lifecycle -------------------------------------------------------
    def connect(self):
        """connect, login once, then enter a character that accepts this label.

        account_busy is tried once more after the client releases a saved
        lease. A benchmark_selection_rejected character is not selected again.
        A missing character is created as a level-1 Human.
        """
        self.game.connect(self.model_name)
        self._login_with_one_busy_retry()
        chars = self.game.characters().get("characters") or []
        if self._enter_existing(chars):
            self._refresh()
            self._load_spells()
            return True
        created = self.game.create_character(self._creation_name(chars), self.char_class)
        guid = created.get("guid") or (created.get("character") or {}).get("guid")
        self.game.select_character(int(guid))
        self._refresh()
        self._load_spells()
        return True

    def _login_with_one_busy_retry(self):
        try:
            self.game.login(self.username, self.password)
        except CowCraftError as exc:
            if getattr(exc, "code", None) != "account_busy":
                raise
            self.game.login(self.username, self.password)

    def _creation_name(self, chars):
        names = {str(entry.get("name", "")).lower() for entry in chars}
        if self.character_name.lower() not in names:
            return self.character_name
        letters = "".join(ch for ch in self.character_name if ch.isalpha())
        return ((letters[:8] or "Human") + "Two")[:12]

    def _enter_existing(self, chars):
        named = []
        rest = []
        wanted = self.character_name.lower()
        for entry in chars:
            guid = entry.get("guid")
            if isinstance(guid, bool) or not isinstance(guid, int):
                continue
            if str(entry.get("name", "")).lower() == wanted:
                named.append(guid)
            else:
                rest.append(guid)
        for guid in named + rest:
            try:
                self.game.select_character(guid)
                return True
            except CowCraftError as exc:
                if getattr(exc, "code", None) == "benchmark_selection_rejected":
                    continue
                raise
        return False

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
        progressed = moved > 0.5 or action in ("attack", "cast", "interact", "loot", "stop")
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
        me = self.state.get("self")
        if isinstance(me, dict) and me.get("x") is not None:
            return me
        for unit in self.state.get("nearby_units", []):
            if unit.get("name", "").lower() == self.character_name.lower():
                return unit
        units = self.state.get("nearby_units", [])
        return units[0] if units else None

    def _digit_guid(self, unit):
        guid = str((unit or {}).get("guid") or "")
        if guid.isdigit() and len(guid) <= 20:
            return guid
        return None

    def _is_hostile(self, unit):
        if not isinstance(unit, dict) or unit.get("dead"):
            return False
        if unit.get("hostile") is True or unit.get("faction") == 32:
            return self._digit_guid(unit) is not None
        return False

    def _nearest_hostile(self):
        best, best_d = None, float("inf")
        for unit in self.state.get("nearby_units", []):
            if not self._is_hostile(unit):
                continue
            dist = math.dist((unit.get("x", 0), unit.get("y", 0)), self._pos[:2])
            if dist < best_d:
                best, best_d = unit, dist
        return best

    def _nearest_corpse(self):
        best, best_d = None, float("inf")
        for unit in self.state.get("nearby_units", []):
            if not isinstance(unit, dict) or not unit.get("dead"):
                continue
            if not (unit.get("hostile") is True or unit.get("faction") == 32):
                continue
            if self._digit_guid(unit) is None:
                continue
            dist = math.dist((unit.get("x", 0), unit.get("y", 0)), self._pos[:2])
            if dist < best_d:
                best, best_d = unit, dist
        return best

    def _power(self):
        me = self._me() or {}
        power = me.get("power", me.get("rage", 0))
        if isinstance(power, bool) or not isinstance(power, (int, float)):
            return 0
        return int(power)

    def _load_spells(self):
        try:
            extra = self.game.observe(spell_limit=40) or {}
        except Exception:
            return
        if isinstance(extra, dict):
            self.spells = learned_spells(extra) or learned_spells(self.state)

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
        elif action in ("attack", "cast"):
            target = self._nearest_hostile()
            guid = self._digit_guid(target)
            if guid:
                self.game.action("target", {"guid": guid})
                spell_id = spell_to_cast(self.spells or learned_spells(self.state), self._power())
                if spell_id is not None:
                    self.game.action("cast", {"spell_id": spell_id, "target_guid": guid})
                self.game.action("attack", {"guid": guid})
        elif action == "stop":
            self.game.action("attack_stop")
        elif action == "interact":
            obj = self._nearest_interactable()
            if obj:
                self.game.action("interact_object", {"guid": obj["guid"]})
        elif action == "loot":
            target = self._nearest_corpse()
            guid = self._digit_guid(target)
            if guid:
                self.game.action("loot_open", {"guid": guid})
                try:
                    extra = self.game.observe(sections=list(LOOT_SECTIONS)) or {}
                except Exception:
                    extra = {}
                if isinstance(extra, dict) and extra:
                    self.state = extra
                    me = self._me()
                    if me:
                        self._pos = (
                            me.get("x", 0.0), me.get("y", 0.0), me.get("z", 0.0))
                        self._hp = (me.get("health", 1), me.get("max_health", 1))
                loot = loot_window(self.state)
                slots, has_money = loot_plan(loot)
                if has_money:
                    self.game.action("loot_money", {})
                for slot in slots:
                    self.game.action("loot_take", {"slot": slot})
                self.game.action("loot_close", {"guid": guid})
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
