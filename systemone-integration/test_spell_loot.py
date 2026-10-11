"""Cast and loot use only what the server already listed."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cowcraft_env import (  # noqa: E402
    LOOT_SECTIONS,
    CowCraftEnv,
    learned_spells,
    loot_plan,
    loot_window,
    spell_to_cast,
)


class SpellLootTest(unittest.TestCase):
    def test_spell_to_cast_uses_only_listed_affordable_spells(self):
        spells = [
            {"name": "Battle Shout", "spell_id": 6673, "power_cost": 10},
            {"name": "Heroic Strike", "spell_id": 78, "power_cost": 15, "harmful": True},
            {"name": "Too Costly", "id": True, "power_cost": 0},
            "not-a-spell",
        ]
        self.assertEqual(spell_to_cast(spells, 20), 78)
        self.assertEqual(spell_to_cast(spells, 10), 6673)
        self.assertIsNone(spell_to_cast(spells, 0))
        self.assertIsNone(spell_to_cast([], 100))
        self.assertIsNone(spell_to_cast([{"name": "Heroic Strike", "spell_id": "78"}], 30))
        listed = learned_spells({
            "spellbook": {"known": [{"id": 78, "name": "Heroic Strike", "rage_cost": 15}]},
        })
        self.assertEqual(spell_to_cast(listed, 15), 78)
        self.assertIsNone(spell_to_cast(learned_spells({"quests": [78]}), 100))
        mixed = [
            {"name": "Shoot", "spell_id": 5019, "power_cost": 0},
            {"name": "Attack", "spell_id": 6603, "power_cost": 0},
            {"name": "Shadow Bolt", "spell_id": 686, "power_cost": 25},
        ]
        self.assertEqual(spell_to_cast(mixed, 109), 686)
        self.assertEqual(spell_to_cast(mixed, 0), 5019)
        print("SPELL cast picks a listed affordable harmful spell and invents none")

    def test_loot_plan_reads_the_window_only(self):
        slots, has_money = loot_plan({
            "gold": 0,
            "items": [
                {"slot": 0, "item_id": 7074},
                {"slot": 1, "item_id": 4865},
                {"slot": True, "item_id": 9},
                {"slot": "2"},
            ],
        })
        self.assertEqual(slots, [0, 1])
        self.assertFalse(has_money)
        slots, has_money = loot_plan({"money": {"gold": 0, "silver": 0, "copper": 3}})
        self.assertEqual(slots, [])
        self.assertTrue(has_money)
        self.assertEqual(loot_plan(None), ([], False))
        self.assertEqual(loot_plan({}), ([], False))
        window = loot_window({
            "ordinary_ui": {"loot": {"gold": 4, "items": [{"slot": 3}]}},
        })
        self.assertEqual(loot_plan(window), ([3], True))
        self.assertEqual(loot_window({"ui": {"open": False}}), {})
        self.assertIn("ui", LOOT_SECTIONS)
        print("LOOT plan takes listed slots and window money only")

    def test_env_casts_listed_spell_and_loots_ui_section(self):
        class FakeGame:
            def __init__(self, state):
                self.state = state
                self.calls = []

            def action(self, name, params=None):
                self.calls.append(("action", name, params or {}))
                return {}

            def observe(self, sections=None, spell_limit=None, spell_offset=None):
                self.calls.append((
                    "observe",
                    None if sections is None else list(sections),
                    spell_limit,
                ))
                return self.state

        with tempfile.TemporaryDirectory() as tmp:
            env = CowCraftEnv(
                gateway_url="http://127.0.0.1:9/mcp",
                username="ada",
                password="n" * 12,
                character_name="Ada",
                token_path=os.path.join(tmp, "connection.txt"),
                model_path=os.path.join(tmp, "model.txt"),
            )
            fight = {
                "self": {
                    "name": "Ada", "x": 0, "y": 0, "z": 0,
                    "health": 40, "max_health": 50, "power": 15,
                },
                "nearby_units": [{
                    "name": "Young Wolf", "guid": "42", "faction": 32,
                    "hostile": False, "dead": False, "x": 1, "y": 1,
                }],
            }
            game = FakeGame(fight)
            env.game = game
            env.state = fight
            env._pos = (0.0, 0.0, 0.0)
            env.spells = [{
                "name": "Heroic Strike", "spell_id": 78, "power_cost": 15,
            }]
            env.act("cast")
            kinds = [call[1] for call in game.calls if call[0] == "action"]
            self.assertEqual(kinds, ["target", "cast", "attack"])
            cast = next(call for call in game.calls if call[1] == "cast")
            self.assertEqual(cast[2]["spell_id"], 78)
            self.assertEqual(cast[2]["target_guid"], "42")

            env.spells = [{"name": "Heroic Strike", "spell_id": 78, "power_cost": 15}]
            fight["self"]["power"] = 0
            env.state = fight
            game.calls.clear()
            env.act("attack")
            kinds = [call[1] for call in game.calls if call[0] == "action"]
            self.assertNotIn("cast", kinds)
            self.assertIn("attack", kinds)

            corpse = {
                "self": {"name": "Ada", "x": 0, "y": 0, "z": 0, "health": 40, "max_health": 50},
                "nearby_units": [{
                    "name": "Young Wolf", "guid": "99", "faction": 32,
                    "dead": True, "x": 1, "y": 1,
                }],
                "ordinary_ui": {
                    "loot": {
                        "gold": 0,
                        "items": [{"slot": 0, "item_id": 4865}, {"slot": 1, "item_id": 7074}],
                    },
                },
            }
            game.state = corpse
            env.state = corpse
            env._pos = (0.0, 0.0, 0.0)
            game.calls.clear()
            env.act("loot")
            observes = [call for call in game.calls if call[0] == "observe"]
            self.assertTrue(any(call[1] == list(LOOT_SECTIONS) for call in observes))
            kinds = [call[1] for call in game.calls if call[0] == "action"]
            self.assertEqual(kinds, ["loot_open", "loot_take", "loot_take", "loot_close"])
            takes = [call[2]["slot"] for call in game.calls if call[1] == "loot_take"]
            self.assertEqual(takes, [0, 1])
            close = next(call for call in game.calls if call[1] == "loot_close")
            self.assertEqual(close[2]["guid"], "99")

            game.calls.clear()
            env.act("stop")
            self.assertIn(("action", "attack_stop", {}), game.calls)
        print("ENV cast a listed spell and looted the ui window slots")


if __name__ == "__main__":
    unittest.main(verbosity=2)
