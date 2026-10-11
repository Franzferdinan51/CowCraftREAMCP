"""Local-model decisions use only models that are already loaded."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auto_model_judge import (  # noqa: E402
    advice_text,
    bounded_world_summary,
    choose_action,
    decision_model_ids,
    executed_action,
    main_model_id,
    prompt_with_advice,
)
from judge import build_questions, choice_criteria, decision_payload  # noqa: E402
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

    def test_decision_advice_does_not_choose_the_action(self):
        loaded = ["main-model", "d1-omni-600m", "d1-3b"]
        self.assertEqual(decision_model_ids(loaded), ["d1-3b", "d1-omni-600m"])
        self.assertEqual(main_model_id(loaded), "main-model")
        self.assertIsNone(main_model_id(["d1-3b", "d1-omni-600m"]))
        advice = advice_text([
            ("d1-3b", "attack the wolf"),
            ("d1-omni-600m", "use shadow bolt"),
        ])
        self.assertIn("not required", advice)
        self.assertIn("shadow bolt", advice)
        self.assertEqual(executed_action("loot", advice), "loot")
        self.assertEqual(executed_action(None, advice), "wait")
        self.assertEqual(executed_action("", advice), "wait")
        shown = prompt_with_advice("self hp=10", advice)
        self.assertIn("self hp=10", shown)
        self.assertIn("not required to follow", shown)
        payload = decision_payload("self hp=10", build_questions(ACTIONS)[0], "d1-3b")
        self.assertIn("not required", payload["instructions"])
        self.assertIn("attack", payload["criteria"])
        self.assertNotIn("d1-3b", payload["criteria"])
        self.assertNotIn("d1-omni-600m", payload["criteria"])
        self.assertEqual(choice_criteria({"options": [{"name": "attack"}]}), {"attack": None})
        print("LOCAL decision advice is optional and the main model chooses")


if __name__ == "__main__":
    unittest.main(verbosity=2)
