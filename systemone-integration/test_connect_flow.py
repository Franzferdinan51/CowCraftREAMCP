"""The bot connect path follows the MCP instructions."""

import os
import subprocess
import sys
import tempfile
import unittest

GATEWAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp-gateway")
sys.path.insert(0, GATEWAY)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import harness  # noqa: E402
from cowcraft_env import (  # noqa: E402
    CowCraftEnv,
    known_account,
    race_for_create,
    should_register,
)
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

    def test_unknown_account_is_invalid_credentials(self):
        client = CowCraft(
            url=self.url,
            token_path=self.path("unknown-conn.txt"),
            model_path=self.path("unknown-model.txt"),
        )
        client.connect("probe")
        with self.assertRaises(CowCraftError) as caught:
            client.login("nobody", harness.fixture_secret())
        self.assertEqual(caught.exception.code, "invalid_credentials")
        client.disconnect()
        print("CONNECT unknown account login is invalid_credentials")

    def _fresh(self, username, character, race=1, appearance=None):
        appearance = appearance or {}
        env = CowCraftEnv(
            gateway_url=self.url,
            username=username,
            password=self.secret,
            character_name=character,
            char_class=1,
            race=race,
            model_name="Label-A",
            token_path=self.path(username + "-conn.txt"),
            model_path=self.path(username + "-model.txt"),
            skin=appearance.get("skin", 0),
            face=appearance.get("face", 0),
            hair_style=appearance.get("hair_style", 0),
            hair_color=appearance.get("hair_color", 0),
            facial_hair=appearance.get("facial_hair", 0),
        )
        return env

    def _calls(self, env, name):
        return [
            item for item in env.game._sent
            if item.get("method") == "tools/call"
            and (item.get("params") or {}).get("name") == name
        ]

    def test_new_account_registers_once_then_creates_the_character(self):
        self.secret = "fresh-account-pw"
        self.assertFalse(should_register(True))
        self.assertTrue(should_register(False))
        counts = []
        for username, character, race in (
            ("Nia", "Niaborn", 1),
            ("Ora", "Oraborn", 2),
        ):
            env = self._fresh(username, character, race=race, appearance={
                "skin": 3, "face": 4, "hair_style": 2, "hair_color": 1, "facial_hair": 0,
            })
            self.assertTrue(env.connect())
            names = [item.get("params", {}).get("name") for item in env.game._sent
                     if item.get("method") == "tools/call"]
            registers = self._calls(env, "register")
            logins = self._calls(env, "login")
            creates = self._calls(env, "create_character")
            selects = self._calls(env, "select_character")
            self.assertEqual(len(registers), 1)
            self.assertEqual(registers[0]["params"]["arguments"]["username"], username)
            self.assertEqual(len(logins), 1)
            self.assertLess(names.index("register"), names.index("login"))
            self.assertEqual(len(creates), 1)
            character_body = creates[0]["params"]["arguments"]["character"]
            self.assertEqual(character_body["race"], race)
            self.assertEqual(character_body["class"], 1)
            self.assertEqual(character_body["skin"], 3)
            self.assertEqual(character_body["face"], 4)
            self.assertEqual(character_body["hair_style"], 2)
            self.assertEqual(character_body["hair_color"], 1)
            self.assertEqual(character_body["facial_hair"], 0)
            self.assertEqual(len(selects), 1)
            guid = selects[0]["params"]["arguments"]["guid"]
            self.assertIsInstance(guid, int)
            self.assertIsInstance(env.entered_guid, int)
            self.assertEqual(guid, env.entered_guid)
            self.assertLess(names.index("create_character"), names.index("select_character"))
            self.assertTrue(known_account(env.game.token_path, username))
            with open(env.game.token_path + ".account", encoding="utf-8") as handle:
                marker = handle.read()
            self.assertNotIn(self.secret, marker)
            counts.append((len(registers), len(logins), len(creates), len(selects)))
            if username == "Ora":
                env.close()
                again = self._fresh(username, character, race=race)
                self.assertTrue(again.connect())
                self.assertEqual(self._calls(again, "register"), [])
                self.assertEqual(self._calls(again, "create_character"), [])
                again_selects = self._calls(again, "select_character")
                self.assertEqual(len(again_selects), 1)
                self.assertEqual(again_selects[0]["params"]["arguments"]["guid"], guid)
                again.close()
            else:
                env.close()
        self.assertEqual(counts[0], counts[1])
        print("CONNECT fresh account registered once and created one character")

    def test_other_race_is_refused_before_create(self):
        self.secret = harness.fixture_secret()
        with self.assertRaises(CowCraftError) as caught:
            race_for_create(7)
        self.assertEqual(caught.exception.code, "invalid_arguments")
        env = self._fresh("Ria", "Riaborn", race=7)
        with self.assertRaises(CowCraftError) as caught:
            env.connect()
        self.assertEqual(caught.exception.code, "invalid_arguments")
        self.assertEqual(self._calls(env, "create_character"), [])
        env.close()
        print("CONNECT race outside 1 and 2 was not sent")

    def test_skill_helper_creates_account_and_character(self):
        secret = "helper-pass-99"
        script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "hermes-integration", "hermes_skill", "scripts", "wow.py",
        )
        token = self.path("helper-conn.txt")
        model = self.path("helper-model.txt")
        base = os.environ.copy()
        base.update({
            "MINTA_USERNAME": "Helpa",
            "MINTA_PASSWORD": secret,
            "MINTA_GATEWAY_URL": self.url,
            "MINTA_TOKEN_PATH": token,
            "MINTA_MODEL_PATH": model,
            "MINTA_AGENT_LABEL": "Label-A",
            "BACKEND": "stub",
        })
        outputs = []
        for cmd in ("account", "character"):
            proc = subprocess.run(
                [sys.executable, script, "--character", "Helpaborn", "--race", "1",
                 "--skin", "1", "--face", "2", cmd],
                env=base,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(secret, proc.stdout)
            self.assertNotIn(secret, proc.stderr)
            outputs.append(proc.stdout)
        combined = "\n".join(outputs)
        self.assertIn("account Helpa", combined)
        self.assertIn("character Helpaborn", combined)
        print(combined)
        print("CONNECT helper created the account and the character")


if __name__ == "__main__":
    unittest.main(verbosity=2)
