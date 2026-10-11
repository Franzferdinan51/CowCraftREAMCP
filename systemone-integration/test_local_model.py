"""Local-model decisions use only models that are already loaded."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_model_judge import (  # noqa: E402
    bounded_world_summary,
    choose_action,
    other_decision_model,
    select_decision_model,
)
from judge import build_questions, choice_criteria, decision_models, decision_payload  # noqa: E402
from cowcraft_env import ACTIONS  # noqa: E402


class LocalModelTest(unittest.TestCase):
    def test_loaded_reply_empty_list_and_garbage(self):
        log = []
        action, same = choose_action(["local-model"], "attack", load_log=log)
        self.assertEqual(action, "attack")
        self.assertIs(same, log)
        self.assertEqual(log, [])

        action, same = choose_action([], "attack", load_log=log)
        self.assertEqual(action, "wait")
        self.assertEqual(log, [])

        action, same = choose_action(["local-model"], "purple elephant", load_log=log)
        self.assertEqual(action, "wait")
        self.assertEqual(log, [])

        action, _log = choose_action(["local-model"], "attacking the camp", load_log=log)
        self.assertEqual(action, "wait")
        self.assertEqual(log, [])
        print("LOCAL valid reply returned attack; empty list and garbage returned wait")

    def test_summary_is_self_hostiles_and_money(self):
        state = {
            "self": {"name": "Ada", "hp": 40, "money": {"copper": 3}},
            "nearby_units": [
                {"name": "Young Wolf", "faction": 32, "hostile": False, "dead": False},
                {"name": "Rabbit", "faction": 31, "hostile": False, "dead": False},
                {"name": "Dead Wolf", "faction": 32, "dead": True},
                {"name": "Boar", "hostile": True, "faction": 14, "dead": False},
            ],
            "quests": ["bring back a long quest text"],
            "nearby_objects": [{"name": "chest"}],
        }
        text = bounded_world_summary(state, limit=600)
        self.assertIn("Ada", text)
        self.assertIn("Young Wolf", text)
        self.assertIn("Boar", text)
        self.assertIn("copper", text)
        self.assertNotIn("Rabbit", text)
        self.assertNotIn("Dead Wolf", text)
        self.assertNotIn("quest", text.lower())
        self.assertNotIn("chest", text)
        self.assertLessEqual(len(text), 600)
        huge = bounded_world_summary(
            {"self": {"name": "N" * 2000, "hp": 1, "money": 0}},
            limit=600,
        )
        self.assertLessEqual(len(huge), 600)
        print("LOCAL summary listed self, hostiles, and money inside 600 characters")

    def test_only_d1_3b_then_d1_omni_are_decision_models(self):
        loaded = ["mimo-v2.6", "d1-omni-600m", "d1-3b-q4"]
        self.assertEqual(select_decision_model(loaded), "d1-3b-q4")
        self.assertEqual(select_decision_model(["llama", "d1-omni-600m"]), "d1-omni-600m")
        self.assertIsNone(select_decision_model(["mimo-v2.6"]))
        self.assertIsNone(select_decision_model([]))
        self.assertEqual(
            other_decision_model("d1-3b-q4", loaded), "d1-omni-600m")
        self.assertIsNone(other_decision_model("d1-3b", ["d1-3b"]))
        self.assertEqual(decision_models(None), ("d1-3b", "d1-omni-600m"))
        self.assertEqual(decision_models("d1-omni-600m"), ("d1-omni-600m",))
        self.assertEqual(decision_models("mimo-v2.6"), ("d1-3b", "d1-omni-600m"))
        payload = decision_payload("self hp=10", build_questions(ACTIONS)[0], "d1-3b")
        self.assertEqual(payload["model"], "d1-3b")
        self.assertEqual(payload["type"], "choice")
        self.assertIn("attack", payload["criteria"])
        self.assertNotIn("options", payload["criteria"])
        for desc in payload["criteria"].values():
            self.assertTrue(desc is None or isinstance(desc, str))
        self.assertEqual(choice_criteria({"options": [{"name": "attack"}]}), {"attack": None})
        print("LOCAL decision models are d1-3b then d1-omni-600m")


if __name__ == "__main__":
    unittest.main(verbosity=2)
