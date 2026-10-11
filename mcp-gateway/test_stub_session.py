"""One stub-gateway session through the shipped client, from initialize."""

import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import gateway  # noqa: E402
import harness  # noqa: E402
from cowcraft_mcp import CowCraft, CowCraftError  # noqa: E402
from tools import tool_names  # noqa: E402

TOKEN_RE = re.compile(r"^[a-f0-9-]{36}\.[a-f0-9]{64}$")
harness.install_production_guard()


class StubSessionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = harness.start_gateway()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        harness.reset_gateway()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def client(self, token_name, model_name, clock=None):
        kwargs = {}
        if clock is not None:
            kwargs["clock"] = clock
            kwargs["sleep"] = clock.sleep
        return CowCraft(
            url=self.url,
            token_path=self.path(token_name),
            model_path=self.path(model_name),
            **kwargs,
        )

    def test_session_from_initialize(self):
        secret = harness.fixture_secret()
        label = "Juno-Model_1"
        client = self.client("conn.txt", "model.txt")

        init = client.initialize()
        self.assertIn("protocolVersion", init)
        self.assertIn("account_busy", init.get("instructions", ""))
        self.assertIn("benchmark_selection_rejected", init["instructions"])
        self.assertIn("Human", init["instructions"])
        print("SESSION initialize ok")

        listed = client.list_tools()
        self.assertIsInstance(listed, dict)
        names = [item["name"] for item in listed["tools"]]
        self.assertEqual(names, tool_names())
        self.assertEqual(len(names), 15)
        self.assertIn("resume", names)
        self.assertIn("observe_map", names)
        for required in ("stop", "disconnect"):
            tool = next(item for item in listed["tools"] if item["name"] == required)
            schema = tool["inputSchema"]
            self.assertIn("connection", schema["properties"])
            self.assertIn("connection", schema["required"])
        print("SESSION tools/list is one object naming all 15 tools")

        token = client.connect(label)
        self.assertIsNotNone(TOKEN_RE.fullmatch(token))
        with open(client.model_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), label + "\n")
        connect_calls = harness.named(client._sent, "connect")
        self.assertEqual(
            connect_calls[-1]["params"]["arguments"]["model_name"],
            label,
        )
        self.assertIn("login", client.instructions)
        self.assertIn("disconnect", client.instructions)
        print("SESSION connect token matches the lease pattern")

        client.register("sessionuser", secret)
        client.login("sessionuser", secret)
        listing = client.characters()
        self.assertIn("characters", listing)
        print("SESSION register login characters ok")

        creates_before = len(harness.named(client._sent, "create_character"))
        with self.assertRaises(CowCraftError) as rejected_race:
            client.create_character("Elfname", race=7)
        self.assertEqual(rejected_race.exception.code, "invalid_arguments")
        self.assertEqual(
            len(harness.named(client._sent, "create_character")),
            creates_before,
        )
        created = client.create_character("Theron", char_class=1, race=1)
        guid = created["character"]["guid"]
        self.assertIs(type(guid), int)
        sent_create = harness.named(client._sent, "create_character")[-1]
        race = sent_create["params"]["arguments"]["character"]["race"]
        self.assertEqual(race, 1)
        self.assertIs(type(race), int)
        orc = client.create_character("Throg", char_class=1, race=2)
        self.assertIs(type(orc["character"]["guid"]), int)
        orc_race = harness.named(client._sent, "create_character")[-1]
        orc_race = orc_race["params"]["arguments"]["character"]["race"]
        self.assertEqual(orc_race, 2)
        print("SESSION create_character sent Human race 1 and Orc race 2")

        selects_before = len(harness.named(client._sent, "select_character"))
        with self.assertRaises(CowCraftError) as rejected_guid:
            client.select_character(str(guid))
        self.assertEqual(rejected_guid.exception.code, "invalid_arguments")
        self.assertEqual(
            len(harness.named(client._sent, "select_character")),
            selects_before,
        )
        client.select_character(guid)
        sent_guid = harness.named(client._sent, "select_character")[-1]
        self.assertIs(type(sent_guid["params"]["arguments"]["guid"]), int)
        print("SESSION integer guid accepted and string guid rejected")

        state = client.observe()
        self.assertTrue(state.get("self"))
        self.assertEqual(state["self"]["name"], "Theron")
        print("SESSION observe returned self state")

        moved = client.action("move", {"x": -8940, "y": -130, "z": 83})
        self.assertTrue(moved.get("moved"))
        actions = client.list_actions()
        self.assertIn("move", actions["actions"])
        print("SESSION action accepted and list_actions returned move")

        clock = harness.ManualClock()
        other = self.client("other.txt", "other-model.txt", clock=clock)
        other.connect(label)
        with self.assertRaises(CowCraftError) as busy:
            other.login("sessionuser", secret)
        self.assertEqual(busy.exception.code, "account_busy")
        self.assertIsNotNone(gateway.leases.get_lease(token)[0])
        print("SESSION second login returned account_busy while the lease was held")

        stopped = client.stop()
        self.assertTrue(stopped.get("stopped"))
        client.disconnect()
        self.assertIsNone(gateway.leases.get_lease(token)[0])
        print("SESSION stop and disconnect ok")

        replay = self.client("replay.txt", "model.txt")
        replayed = replay.connect()
        self.assertEqual(
            harness.named(replay._sent, "connect")[-1]["params"]["arguments"]["model_name"],
            label,
        )
        lease, err = gateway.leases.get_lease(replayed)
        self.assertIsNone(err)
        self.assertEqual(lease.model_name, label)
        replay.login("sessionuser", secret)
        replay.select_character(guid)
        replay.disconnect()
        print("SESSION stored model_name was resent unchanged")

        mismatched = self.client("mismatch.txt", "mismatch-model.txt")
        mismatched.connect("Other-Model_2")
        mismatched.login("sessionuser", secret)
        with self.assertRaises(CowCraftError) as rejected:
            mismatched.select_character(guid)
        self.assertEqual(rejected.exception.code, "benchmark_selection_rejected")
        sent = len(harness.named(mismatched._sent, "select_character"))
        self.assertEqual(sent, 1)
        with self.assertRaises(CowCraftError) as again:
            mismatched.select_character(guid)
        self.assertEqual(again.exception.code, "benchmark_selection_rejected")
        self.assertEqual(len(harness.named(mismatched._sent, "select_character")), 1)
        mismatched.disconnect()
        print("SESSION benchmark_selection_rejected was surfaced once")

        deleter = self.client("delete.txt", "model.txt")
        deleter.connect()
        deleter.login("sessionuser", secret)
        deleted = deleter.delete_character(guid, "Theron")
        self.assertEqual(deleted["deleted"], guid)
        deleter.disconnect()
        print("SESSION delete_character ran")


if __name__ == "__main__":
    unittest.main(verbosity=2)
