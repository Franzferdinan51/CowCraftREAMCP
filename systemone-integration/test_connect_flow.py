"""The bot connect path follows the MCP instructions."""

import os
import sys
import tempfile
import unittest

GATEWAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp-gateway")
sys.path.insert(0, GATEWAY)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import harness  # noqa: E402
from cowcraft_env import CowCraftEnv  # noqa: E402
from cowcraft_mcp import CowCraft, CowCraftError  # noqa: E402

harness.install_production_guard()


class ConnectFlowTest(unittest.TestCase):
    def setUp(self):
        harness.reset_gateway()
        self.server, self.url = harness.start_gateway()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_initialize_instructions_name_the_login_order(self):
        client = CowCraft(
            url=self.url,
            token_path=self.path("conn.txt"),
            model_path=self.path("model.txt"),
        )
        init = client.initialize()
        text = init["instructions"]
        self.assertIn("connect", text)
        self.assertIn("login", text)
        self.assertIn("account_busy", text)
        self.assertIn("not rate_limited", text)
        self.assertIn("60 seconds", text)
        self.assertIn("benchmark_selection_rejected", text)
        self.assertIn("Human", text)
        self.assertIn("disconnect", text)
        print("CONNECT initialize instructions name the login order")

    def test_env_skips_a_rejected_character_and_enters_another(self):
        secret = harness.fixture_secret()
        setup = CowCraft(
            url=self.url,
            token_path=self.path("setup-conn.txt"),
            model_path=self.path("setup-model.txt"),
        )
        setup.connect("Label-A")
        setup.register("ada", secret)
        setup.disconnect()

        first = CowCraftEnv(
            gateway_url=self.url,
            username="ada",
            password=secret,
            character_name="Theron",
            model_name="Label-A",
            token_path=self.path("a-conn.txt"),
            model_path=self.path("a-model.txt"),
        )
        self.assertTrue(first.connect())
        listing = first.game.characters()
        theron = next(
            entry["guid"] for entry in listing["characters"]
            if entry["name"] == "Theron"
        )
        first.close()

        other = CowCraftEnv(
            gateway_url=self.url,
            username="ada",
            password=secret,
            character_name="Theron",
            model_name="Label-B",
            token_path=self.path("b-conn.txt"),
            model_path=self.path("b-model.txt"),
        )
        self.assertTrue(other.connect())
        selects = [
            item for item in other.game._sent
            if item.get("params", {}).get("name") == "select_character"
        ]
        theron_sends = [
            item for item in selects
            if item["params"]["arguments"]["guid"] == theron
        ]
        self.assertEqual(len(theron_sends), 1)
        self.assertGreater(len(selects), 1)
        names = [entry["name"] for entry in other.game.characters()["characters"]]
        self.assertIn("TheronTwo", names)
        other.close()
        print("CONNECT rejected character was not retried; another character entered")

    def test_env_retries_a_busy_login_once(self):
        secret = harness.fixture_secret()
        holder = CowCraftEnv(
            gateway_url=self.url,
            username="bao",
            password=secret,
            character_name="Mara",
            model_name="Label-A",
            token_path=self.path("hold-conn.txt"),
            model_path=self.path("hold-model.txt"),
        )
        setup = CowCraft(
            url=self.url,
            token_path=self.path("reg-conn.txt"),
            model_path=self.path("reg-model.txt"),
        )
        setup.connect("Label-A")
        setup.register("bao", secret)
        setup.disconnect()
        self.assertTrue(holder.connect())

        second = CowCraftEnv(
            gateway_url=self.url,
            username="bao",
            password=secret,
            character_name="Mara",
            model_name="Label-A",
            token_path=self.path("busy-conn.txt"),
            model_path=self.path("busy-model.txt"),
        )
        with self.assertRaises(CowCraftError) as caught:
            second.connect()
        self.assertEqual(caught.exception.code, "account_busy")
        logins = [
            item for item in second.game._sent
            if item.get("params", {}).get("name") == "login"
        ]
        self.assertEqual(len(logins), 2)
        holder.close()
        print("CONNECT account_busy was retried once and was not rate_limited")


if __name__ == "__main__":
    unittest.main(verbosity=2)
