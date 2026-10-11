"""Read a CowCraft observe body the way the live server sends it.

Position, health, and money live on state["self"]. Top-level x and y are
not the player. Attackability uses the server hostile boolean when one is
sent, and otherwise vanilla 1.12 faction-template reaction. Creature display
names are not consulted.
"""

from __future__ import annotations

import math

# Classic 1.12 UNIT_NPC_FLAG. Bit 1024 is the tabard designer.
QUEST_GIVER = 2
VENDOR = 4
TRAINER = 16
AUCTIONEER = 4096
SERVICE_NPC = QUEST_GIVER | VENDOR | TRAINER | AUCTIONEER

# Faction group bits from FactionTemplate.dbc.
PLAYER_BIT = 1
ALLIANCE_BIT = 2
HORDE_BIT = 4
MONSTER_BIT = 8

# template id -> (faction id, ourMask, friendlyMask, hostileMask, enemies, friends)
# Early 1.12 rows. Enemy and friend lists are Faction.dbc ids, not template ids.
# Human players are template 1. Orc players are template 2.
_TEMPLATES = {
    1: (1, 3, 2, 12, (), ()),
    2: (2, 5, 4, 10, (), ()),
    3: (3, 3, 2, 12, (), ()),
    4: (4, 3, 2, 12, (), ()),
    5: (5, 5, 4, 10, (), ()),
    6: (6, 5, 4, 10, (), ()),
    7: (7, 0, 0, 0, (), ()),
    10: (40, 3, 2, 0, (), ()),
    11: (72, 3, 2, 12, (), ()),
    12: (72, 2, 2, 4, (), (72,)),
    14: (14, 8, 0, 1, (), ()),
    15: (7, 0, 0, 0, (), (7,)),
    16: (14, 8, 0, 1, (), (14,)),
    17: (15, 8, 0, 1, (), (15,)),
    18: (19, 8, 0, 1, (), (19,)),
    19: (17, 8, 0, 1, (), (17,)),
    20: (16, 8, 0, 1, (), (16,)),
    21: (20, 8, 0, 1, (), (20,)),
    22: (22, 8, 0, 1, (), (22,)),
    23: (54, 2, 2, 4, (), ()),
    24: (24, 8, 0, 1, (), (24,)),
    25: (25, 8, 0, 0, (), (25,)),
    26: (25, 8, 0, 1, (), (25,)),
    27: (15, 8, 0, 1, (1,), (15,)),
    28: (26, 8, 0, 1, (), (26,)),
    29: (76, 4, 4, 2, (), ()),
    30: (27, 8, 0, 1, (), (27,)),
    31: (28, 0, 0, 0, (973,), (148, 28)),
    32: (29, 0, 0, 0, (28,), ()),
    33: (40, 5, 4, 0, (), ()),
    34: (15, 8, 0, 0, (), (15,)),
    35: (31, 0, 1, 0, (), (31,)),
    36: (32, 0, 0, 0, (), (32,)),
    37: (33, 8, 0, 1, (), (33,)),
    38: (29, 8, 0, 1, (28,), (29,)),
    39: (18, 8, 0, 1, (), (18,)),
    40: (34, 8, 0, 1, (), (34,)),
    41: (36, 8, 0, 1, (36,), (35,)),
    42: (36, 1, 1, 0, (), (36,)),
    43: (36, 8, 0, 0, (36,), (35,)),
    44: (37, 8, 0, 1, (), (37,)),
    66: (55, 8, 0, 1, (28,), (55,)),
}

PLAYER_TEMPLATES = {1, 2, 3, 4, 5, 6}
_MASK_FIELDS = (
    "our_mask", "ourMask", "friendly_mask", "friendlyMask",
    "hostile_mask", "hostileMask",
)


def self_body(state):
    """The player object. Top-level x/y on the observe body are not it."""
    if not isinstance(state, dict):
        return {}
    me = state.get("self")
    return me if isinstance(me, dict) else {}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def player_xyz(state):
    """(x, y, z) from state["self"]. Missing axes are None, not zero."""
    me = self_body(state)
    return _number(me.get("x")), _number(me.get("y")), _number(me.get("z"))


def purse_copper(state):
    """Total copper. self.money may be an int or a gold/silver/copper dict."""
    me = self_body(state)
    money = me.get("money", state.get("money") if isinstance(state, dict) else None)
    if isinstance(money, bool) or money is None:
        return None
    if isinstance(money, int):
        return money
    if isinstance(money, float):
        return int(money)
    if isinstance(money, dict):
        if not any(key in money for key in ("gold", "silver", "copper")):
            return None
        gold = int(money.get("gold") or 0)
        silver = int(money.get("silver") or 0)
        copper = int(money.get("copper") or 0)
        return gold * 10000 + silver * 100 + copper
    return None


def npc_flags(unit):
    try:
        return int((unit or {}).get("npc_flags") or 0)
    except (TypeError, ValueError):
        return 0


def _has_flag(unit, bit):
    return bool(npc_flags(unit) & bit)


def is_quest_giver(unit):
    return _has_flag(unit, QUEST_GIVER)


def is_vendor(unit):
    return _has_flag(unit, VENDOR)


def is_trainer(unit):
    return _has_flag(unit, TRAINER)


def is_auctioneer(unit):
    return _has_flag(unit, AUCTIONEER)


def is_service_npc(unit):
    """Quest giver, vendor, trainer, or auctioneer. Tabard designer is not one."""
    return bool(npc_flags(unit) & SERVICE_NPC)


def _faction_id(unit):
    if not isinstance(unit, dict):
        return None
    for key in ("faction", "faction_template", "faction_id"):
        if key not in unit or unit.get(key) is None:
            continue
        try:
            return int(unit.get(key))
        except (TypeError, ValueError):
            return None
    return None


def _mask(unit, *keys):
    if not isinstance(unit, dict):
        return None
    for key in keys:
        if key not in unit or unit.get(key) is None:
            continue
        try:
            return int(unit.get(key))
        except (TypeError, ValueError):
            return None
    return None


def _id_list(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    out = []
    for item in value:
        try:
            if isinstance(item, bool):
                continue
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _template_from_row(row):
    faction, our, friendly, hostile, enemies, friends = row
    return {
        "faction": faction,
        "our": our,
        "friendly": friendly,
        "hostile": hostile,
        "enemies": enemies,
        "friends": friends,
    }


def _template_from_fields(unit):
    """Masks carried on the observe unit override the local table."""
    our = _mask(unit, "our_mask", "ourMask")
    friendly = _mask(unit, "friendly_mask", "friendlyMask")
    hostile = _mask(unit, "hostile_mask", "hostileMask")
    if our is None and friendly is None and hostile is None:
        return None
    faction = _mask(unit, "reputation_faction", "faction_dbc")
    if faction is None:
        faction = 0
    return {
        "faction": faction,
        "our": our or 0,
        "friendly": friendly or 0,
        "hostile": hostile or 0,
        "enemies": _id_list(unit.get("enemy_factions") or unit.get("enemies")),
        "friends": _id_list(unit.get("friend_factions") or unit.get("friends")),
    }


def faction_template(unit):
    """Vanilla template for a unit dict or a numeric template id."""
    if isinstance(unit, int) and not isinstance(unit, bool):
        row = _TEMPLATES.get(unit)
        return _template_from_row(row) if row else None
    if not isinstance(unit, dict):
        return None
    explicit = _template_from_fields(unit)
    if explicit is not None:
        return explicit
    number = _faction_id(unit)
    if number is None:
        return None
    row = _TEMPLATES.get(number)
    return _template_from_row(row) if row else None


def _player_template(player):
    if player is None:
        return _template_from_row(_TEMPLATES[1])
    if isinstance(player, int) and not isinstance(player, bool):
        found = faction_template(player)
        return found if found is not None else _template_from_row(_TEMPLATES[1])
    if not isinstance(player, dict):
        return _template_from_row(_TEMPLATES[1])
    explicit = _template_from_fields(player)
    if explicit is not None:
        return explicit
    number = _faction_id(player)
    if number in _TEMPLATES:
        return _template_from_row(_TEMPLATES[number])
    race = player.get("race")
    try:
        race = int(race)
    except (TypeError, ValueError):
        race = 1
    if race == 2:
        return _template_from_row(_TEMPLATES[2])
    return _template_from_row(_TEMPLATES[1])


def _linked(ids, faction):
    return bool(faction) and faction in ids


def _side_hostile(source, other):
    if _linked(source["friends"], other["faction"]):
        return False
    if _linked(source["enemies"], other["faction"]):
        return True
    return (source["hostile"] & other["our"]) != 0


def _side_friendly(source, other):
    if _linked(source["enemies"], other["faction"]):
        return False
    if _linked(source["friends"], other["faction"]):
        return True
    return (source["friendly"] & other["our"]) != 0


def faction_reaction_hostile(unit, player=None):
    """True when vanilla 1.12 reaction to the player is hostile.

    Hostile when either side has (hostileMask & other.ourMask) or an enemy
    faction link, unless either side is friendly by mask or a friend link.
    """
    source = faction_template(unit)
    other = _player_template(player)
    if source is None or other is None:
        return False
    friendly = _side_friendly(source, other) or _side_friendly(other, source)
    hostile = _side_hostile(source, other) or _side_hostile(other, source)
    if friendly:
        return False
    return hostile


def _server_reaction(unit):
    """Boolean from hostile or reaction. None when the server did not say."""
    flag = unit.get("hostile")
    if isinstance(flag, bool):
        return flag
    reaction = unit.get("reaction")
    if isinstance(reaction, bool):
        return reaction
    if isinstance(reaction, str):
        text = reaction.strip().lower()
        if text in {"hostile", "hated"}:
            return True
        if text in {"friendly", "honored", "revered", "exalted", "neutral", "unfriendly"}:
            return False
    return None


def is_attackable(unit, player=None):
    """A living unit whose reaction to the player is hostile.

    Dead, immune, and non-attackable units are not targets. Quest givers,
    vendors, trainers, and auctioneers are not targets. A boolean hostile or
    reaction field from the server overrides the faction-template table.
    """
    if not isinstance(unit, dict) or unit.get("dead"):
        return False
    if unit.get("immune") or unit.get("attackable") is False:
        return False
    if is_service_npc(unit):
        return False
    server = _server_reaction(unit)
    if server is not None:
        return server
    return faction_reaction_hostile(unit, player)


def is_player_unit(unit):
    """Classic player faction templates. npc_flags 0 does not mean player."""
    if not isinstance(unit, dict) or unit.get("dead"):
        return False
    number = _faction_id(unit)
    return number in PLAYER_TEMPLATES


def is_lootable(unit):
    """Only a dead unit the server marked loot_eligible."""
    return (
        isinstance(unit, dict)
        and unit.get("dead") is True
        and unit.get("loot_eligible") is True
    )


def quest_offer(state):
    """interaction_ui.quest when a giver dialog is open."""
    if not isinstance(state, dict):
        return None
    ui = state.get("interaction_ui")
    if not isinstance(ui, dict):
        return None
    quest = ui.get("quest")
    return quest if isinstance(quest, dict) else None


def horiz(x, y, tx, ty):
    return math.hypot(float(tx) - float(x), float(ty) - float(y))


def clamp_step(x, y, z, tx, ty, tz, limit):
    """A point at most `limit` yards from (x, y) toward the target."""
    dx = float(tx) - float(x)
    dy = float(ty) - float(y)
    dz = float(tz) - float(z)
    dist = math.hypot(dx, dy)
    if dist <= limit or dist == 0:
        return float(tx), float(ty), float(tz)
    scale = float(limit) / dist
    return float(x) + dx * scale, float(y) + dy * scale, float(z) + dz * scale


def is_stuck(me):
    if not isinstance(me, dict):
        return False
    if me.get("stuck") is True:
        return True
    movement = me.get("movement")
    if isinstance(movement, dict):
        return str(movement.get("status") or "").lower() == "stuck"
    return False
