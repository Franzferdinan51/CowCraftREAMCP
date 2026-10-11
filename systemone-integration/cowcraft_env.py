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
    from cowcraft_world import is_attackable, is_lootable, player_xyz
except ImportError:  # pragma: no cover
    CowCraft = None
    CowCraftError = Exception
    def is_attackable(_unit):
        return False
    def is_lootable(_unit):
        return False
    def player_xyz(_state):
        return None, None, None


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
    "stats",
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


def account_marker_path(token_path):
    """Username-only file beside the lease. It never holds a password."""
    return os.fspath(token_path) + ".account"


def known_account(token_path, username):
    """True when this client already registered or recognized the username."""
    wanted = str(username or "").strip().lower()
    if not wanted or not token_path:
        return False
    try:
        with open(account_marker_path(token_path), encoding="utf-8") as handle:
            text = handle.read().strip().lower()
    except OSError:
        return False
    return text == wanted


def remember_account(token_path, username):
    """Record the username so a later connect does not register again."""
    path = account_marker_path(token_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(str(username).strip() + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def should_register(known):
    """A new account is registered before login. A known one is not."""
    return not known


def race_for_create(race):
    """Human 1 or Orc 2. Any other race is refused before a create is sent."""
    try:
        number = int(race)
    except (TypeError, ValueError):
        number = 0
    if number not in (1, 2):
        raise CowCraftError(
            "create_character: invalid_arguments: race must be 1 (Human) or 2 (Orc)",
            code="invalid_arguments",
        )
    return number


def appearance_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_int_stat(value):
    """Integer from a live stat field. None when the field is absent."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def money_purse(state):
    """Gold, silver, copper, and total copper from an observe body.

    Reads self.money (dict or total copper), then self.gold/silver/copper.
    Returns None when the observe did not include a purse.
    """
    state = state if isinstance(state, dict) else {}
    me = state.get("self") if isinstance(state.get("self"), dict) else {}
    money = me.get("money", state.get("money"))
    if isinstance(money, str) and money.strip():
        return money.strip()
    gold = silver = copper = None
    if isinstance(money, dict):
        gold = as_int_stat(money.get("gold"))
        silver = as_int_stat(money.get("silver"))
        copper = as_int_stat(money.get("copper"))
    elif as_int_stat(money) is not None:
        total = as_int_stat(money)
        gold, silver, copper = total // 10000, (total % 10000) // 100, total % 100
    else:
        gold = as_int_stat(me.get("gold"))
        silver = as_int_stat(me.get("silver"))
        copper = as_int_stat(me.get("copper"))
    if gold is None and silver is None and copper is None:
        return None
    gold = 0 if gold is None else gold
    silver = 0 if silver is None else silver
    copper = 0 if copper is None else copper
    total = gold * 10000 + silver * 100 + copper
    return gold, silver, copper, total


def format_gold(state):
    """Readable purse. Zero is 0g 0s 0c. A missing purse says gold=missing."""
    purse = money_purse(state)
    if purse is None:
        return "gold=missing"
    if isinstance(purse, str):
        return "gold=%s" % purse
    gold, silver, copper, total = purse
    return "gold=%dg %ds %dc (%d copper)" % (gold, silver, copper, total)


def format_self_stats(state):
    """One line of the character's own stats, including gold."""
    state = state if isinstance(state, dict) else {}
    me = state.get("self") if isinstance(state.get("self"), dict) else {}
    parts = ["self name=%s" % (me.get("name") or "")]
    if me.get("level") not in (None, ""):
        parts.append("level=%s" % me.get("level"))
    hp = me.get("health", me.get("hp", ""))
    max_hp = me.get("max_health", me.get("max_hp", ""))
    parts.append("hp=%s/%s" % (hp, max_hp))
    power = me.get("power", me.get("rage", me.get("mana", "")))
    max_power = me.get("max_power", me.get("max_rage", me.get("max_mana", "")))
    if power not in ("", None) or max_power not in ("", None):
        kind = me.get("power_type") or ""
        parts.append("power=%s/%s%s" % (power, max_power, (" " + str(kind)) if kind else ""))
    map_name = me.get("map") or me.get("zone") or ""
    if map_name:
        parts.append("map=%s" % map_name)
    try:
        parts.append("pos=(%.1f,%.1f,%.1f)" % (float(me["x"]), float(me["y"]), float(me["z"])))
    except (KeyError, TypeError, ValueError):
        pass
    parts.append(format_gold(state))
    flags = []
    for key in ("combat", "dead", "ghost", "stuck", "moving"):
        if me.get(key):
            flags.append(key)
    if flags:
        parts.append(" ".join(flags))
    return " ".join(parts)


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
        race=1,
        gender=0,
        skin=0,
        face=0,
        hair_style=0,
        hair_color=0,
        facial_hair=0,
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
        self.race = race
        self.gender = appearance_int(gender)
        self.skin = appearance_int(skin)
        self.face = appearance_int(face)
        self.hair_style = appearance_int(hair_style)
        self.hair_color = appearance_int(hair_color)
        self.facial_hair = appearance_int(facial_hair)
        self.entered_guid = None

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
        """Register a new account, login once, then enter or create a character.

        Registration happens before login so a missing account does not burn
        the 60-second login floor. A known account is not registered again.
        account_busy is tried once more after the client releases a saved
        lease. A benchmark_selection_rejected character is not selected again.
        A character is created only when none on the account can be entered.
        """
        self.game.connect(self.model_name)
        self._register_if_new()
        self._login_with_one_busy_retry()
        chars = self.game.characters().get("characters") or []
        if self._enter_existing(chars):
            self._refresh()
            self._load_spells()
            return True
        self._create_and_select(chars)
        self._refresh()
        self._load_spells()
        return True

    def _register_if_new(self):
        if not should_register(known_account(self.game.token_path, self.username)):
            return
        try:
            self.game.register(self.username, self.password)
        except CowCraftError as exc:
            if getattr(exc, "code", None) != "username_taken":
                raise
        remember_account(self.game.token_path, self.username)

    def _create_and_select(self, chars):
        race = race_for_create(self.race)
        created = self.game.create_character(
            self._creation_name(chars),
            self.char_class,
            gender=self.gender,
            race=race,
            skin=self.skin,
            face=self.face,
            hair_style=self.hair_style,
            hair_color=self.hair_color,
            facial_hair=self.facial_hair,
        )
        guid = created.get("guid") if isinstance(created, dict) else None
        if not isinstance(guid, int):
            nested = created.get("character") if isinstance(created, dict) else None
            guid = nested.get("guid") if isinstance(nested, dict) else None
        self.entered_guid = guid
        self.game.select_character(guid)

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
                self.entered_guid = guid
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
        progressed = moved > 0.5 or action in (
            "attack", "cast", "interact", "loot", "stop", "stats")
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
        x, y, z = player_xyz(self.state)
        if x is not None and y is not None and z is not None:
            self._pos = (x, y, z)
        elif me:
            self._pos = (me.get("x", 0.0), me.get("y", 0.0), me.get("z", 0.0))
        if me:
            self._hp = (me.get("health", 1), me.get("max_health", 1))

    def _me(self):
        me = self.state.get("self") if isinstance(self.state, dict) else None
        if isinstance(me, dict):
            return me
        return None

    def _digit_guid(self, unit):
        guid = str((unit or {}).get("guid") or "")
        if guid.isdigit() and len(guid) <= 20:
            return guid
        return None

    def _is_hostile(self, unit):
        player = {"race": self.race}
        if not is_attackable(unit, player):
            return False
        return self._digit_guid(unit) is not None

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
            if not is_lootable(unit):
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
        elif action == "stats":
            pass
        elif action == "wait":
            time.sleep(1.0)
        else:
            raise ValueError(f"unknown action: {action}")

    def _render(self):
        """Render gateway state as compact text for the judge."""
        x, y, z = self._pos
        hp, max_hp = self._hp
        lines = [
            "role=%s %s" % (self.role, format_self_stats(self.state)),
            "tracked pos=(%.1f,%.1f,%.1f) hp=%s/%s" % (x, y, z, hp, max_hp),
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
