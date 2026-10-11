"""Position, hostiles, loot, path preview, and quests. No live server."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import harness  # noqa: E402
from cowcraft_mcp import CowCraft  # noqa: E402
from cowcraft_nav import (  # noqa: E402
    ARRIVE_YARDS,
    PATH_CLAMP_YARDS,
    STEP_YARDS,
    Nav,
)
from cowcraft_world import (  # noqa: E402
    is_attackable,
    is_auctioneer,
    is_lootable,
    is_quest_giver,
    is_trainer,
    is_vendor,
    player_xyz,
    purse_copper,
)


class WorldReadTest(unittest.TestCase):
    def test_position_and_copper_come_from_self(self):
        state = {
            "x": None,
            "y": None,
            "self": {
                "x": -8894.6,
                "y": -117.8,
                "z": 81.9,
                "health": 68,
                "max_health": 68,
                "level": 2,
                "money": 115,
            },
        }
        self.assertEqual(player_xyz(state), (-8894.6, -117.8, 81.9))
        self.assertEqual(purse_copper(state), 115)
        self.assertEqual(purse_copper({"self": {"money": {"gold": 0, "silver": 1, "copper": 15}}}), 115)
        self.assertIsNone(player_xyz({"x": 1, "y": 2})[0])

    def test_hostile_classifier_uses_faction_reaction(self):
        # Template 14 is hostile to players and is outside the old id pair.
        unlisted = {"name": "Zzax", "hostile": None, "faction": 14, "dead": False}
        also = {"name": "Marblehide", "faction": 22}
        beast_vendor = {
            "name": "Young Wolf", "hostile": True, "faction": 14,
            "npc_flags": 4, "dead": False,
        }
        beast_friendly = {
            "name": "Young Wolf", "hostile": None, "faction": 12, "npc_flags": 0,
        }
        giver = {"name": "Deputy Rainer", "npc_flags": 2}
        corpse = {"name": "Young Wolf", "dead": True, "loot_eligible": False}
        eligible = {"name": "Young Wolf", "dead": True, "loot_eligible": True}
        self.assertTrue(is_attackable(unlisted))
        self.assertTrue(is_attackable(also))
        self.assertTrue(is_attackable({
            "name": "Neverlisted", "faction": 9000, "hostile": None,
            "our_mask": 8, "hostile_mask": 1,
        }))
        self.assertFalse(is_attackable(beast_vendor))
        self.assertFalse(is_attackable(beast_friendly))
        self.assertFalse(is_attackable({
            "name": "Young Wolf", "faction": 32, "hostile": None,
        }))
        self.assertFalse(is_attackable({
            "name": "Young Wolf", "faction": 32, "hostile": False,
        }))
        self.assertFalse(is_attackable({"faction": 14, "hostile": False}))
        self.assertTrue(is_attackable({"faction": 12, "hostile": True}))
        self.assertFalse(is_attackable({"faction": 12}, player={"race": 1}))
        self.assertTrue(is_attackable({"faction": 12}, player={"race": 2}))
        self.assertFalse(is_attackable({"name": "Rabbit", "faction": 31, "hostile": None}))
        self.assertFalse(is_attackable({"faction": 35, "name": "Young Wolf"}))
        self.assertFalse(is_attackable(corpse))
        self.assertTrue(is_quest_giver(giver))
        self.assertTrue(is_vendor({"npc_flags": 4}))
        self.assertTrue(is_vendor({"npc_flags": 16388}))
        self.assertTrue(is_trainer({"npc_flags": 16}))
        self.assertTrue(is_auctioneer({"npc_flags": 4096}))
        self.assertFalse(is_auctioneer({"npc_flags": 1024}))
        self.assertFalse(is_lootable(corpse))
        self.assertTrue(is_lootable(eligible))
        print("WORLD position is self and hostiles use faction reaction")


class ScriptClient:
    def __init__(self, state, previews=None, offers=None):
        self.state = state
        self.previews = list(previews or [])
        self.offers = list(offers or [])
        self.calls = []

    def observe(self, **kwargs):
        self.calls.append(("observe", kwargs))
        if self.offers:
            self.state = self.offers.pop(0)
        return self.state

    def action(self, name, params=None):
        params = dict(params or {})
        self.calls.append((name, params))
        if name == "path_preview":
            return self.previews.pop(0) if self.previews else {"waypoints": []}
        if name == "loot_open":
            return {"items": [{"slot": 0}, {"slot": 2}], "copper": 3}
        return {}


class NavTest(unittest.TestCase):
    def test_goto_clamps_to_35_yards_and_walks_waypoints(self):
        client = ScriptClient(
            {"self": {"x": 0, "y": 0, "z": 0}},
            previews=[{
                "path_status": "ok",
                "waypoints": [{"x": 10, "y": 0, "z": 0}, {"x": 35, "y": 0, "z": 0}],
            }],
        )
        client.offers = [{"self": {"x": 97, "y": 0, "z": 0}}]
        result = Nav(client).goto(100, 0, 0)
        preview = next(call for call in client.calls if call[0] == "path_preview")
        self.assertAlmostEqual(preview[1]["x"], PATH_CLAMP_YARDS)
        moves = [call for call in client.calls if call[0] == "move"]
        self.assertEqual([call[1]["x"] for call in moves], [10, 35])
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "arrived")
        self.assertLessEqual(abs(97 - 100), ARRIVE_YARDS)

    def test_goto_arrives_and_step_is_five_yards(self):
        client = ScriptClient(
            {"self": {"x": 0.0, "y": 0.0, "z": 0.0}},
            previews=[{"waypoints": [{"x": 30, "y": 0, "z": 0}]}],
        )
        client.offers = [{"self": {"x": 31.0, "y": 0.0, "z": 0.0}}]
        nav = Nav(client)
        result = nav.goto(34, 0, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["reason"], "arrived")
        step = ScriptClient({"self": {"x": 0.0, "y": 0.0, "z": 1.0}})
        moved = Nav(step).step_toward(100, 0, 1)
        self.assertTrue(moved["ok"])
        move = next(call for call in step.calls if call[0] == "move")
        self.assertAlmostEqual(move[1]["x"], STEP_YARDS)
        self.assertAlmostEqual(move[1]["y"], 0.0)

    def test_stuck_stops_the_walk(self):
        client = ScriptClient(
            {"self": {"x": 0, "y": 0, "z": 0}},
            previews=[{"waypoints": [{"x": 10, "y": 0, "z": 0}]}],
        )
        client.offers = [{"self": {"x": 1, "y": 0, "z": 0, "movement": {"status": "stuck"}}}]
        result = Nav(client).goto(30, 0, 0)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "stuck")

    def test_loot_and_quest_use_the_live_actions(self):
        corpse = {"guid": "99", "dead": True, "loot_eligible": True}
        client = ScriptClient({"self": {"x": 0, "y": 0, "z": 0}})
        looted = Nav(client).loot_corpse(corpse)
        kinds = [call[0] for call in client.calls]
        self.assertEqual(kinds, ["loot_open", "loot_money", "loot_take", "loot_take", "loot_close"])
        self.assertEqual(client.calls[0][1]["guid"], "99")
        self.assertEqual(client.calls[-1][1]["guid"], "99")
        self.assertEqual(looted["slots"], [0, 2])
        skipped = Nav(client).loot_corpse({"guid": "99", "dead": True, "loot_eligible": False})
        self.assertFalse(skipped["looted"])

        giver = {"guid": "7", "x": 1, "y": 1, "z": 0, "npc_flags": 2}
        quests = ScriptClient({"self": {"x": 1, "y": 1, "z": 0}})
        quests.offers = [{
            "self": {"x": 1, "y": 1, "z": 0},
            "interaction_ui": {
                "quest": {"quest_id": 33, "giver_guid": "7", "can_accept": True},
            },
        }]
        accepted = Nav(quests).accept_quest(giver)
        actions = [call for call in quests.calls if call[0] != "observe"]
        self.assertEqual(actions[0], ("interact", {"guid": "7"}))
        self.assertEqual(actions[1], ("quest_accept", {"guid": "7", "quest_id": 33}))
        self.assertTrue(accepted["accepted"])
        sold = Nav(quests).sell("12", "88", 2)
        self.assertEqual(sold, {})
        self.assertEqual(quests.calls[-1], ("vendor_sell", {
            "guid": "12", "item_guid": "88", "count": 2,
        }))
        print("NAV walks clamped waypoints, loots eligible corpses, and accepts the open quest")


class ClientSelfTest(unittest.TestCase):
    def test_client_self_state_reads_self_not_a_nearby_unit(self):
        secret_url = "http://127.0.0.1:9/mcp"
        with tempfile.TemporaryDirectory() as tmp:
            transport = harness.FakeTransport([
                harness.tool_result({
                    "x": None,
                    "y": None,
                    "self": {"x": 5, "y": 6, "z": 7, "money": 40, "level": 2},
                    "nearby_units": [{"name": "Young Wolf", "x": 1, "y": 1, "faction": 17}],
                }),
            ])
            client = CowCraft(
                url=secret_url,
                token_path=os.path.join(tmp, "connection.txt"),
                model_path=os.path.join(tmp, "model.txt"),
                transport=transport,
            )
            client.connection = "session-token"
            me = client.self_state()
            self.assertEqual(me["x"], 5)
            self.assertEqual(me["money"], 40)
            self.assertNotEqual(me.get("name"), "Young Wolf")
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("CLIENT self_state reads state.self")


if __name__ == "__main__":
    unittest.main(verbosity=2)
