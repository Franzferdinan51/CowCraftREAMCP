"""Lease recovery against the shipped client and the stub gateway.

A connection token is written to the token file (default
/tmp/cowcraft_conn.txt; tests inject another path). A later process that
has only that file can disconnect it. A fresh token does not.
account_busy disconnects the saved token, then waits, and is not
rate_limited.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from http.client import IncompleteRead

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import gateway  # noqa: E402
import harness  # noqa: E402
from backend import BackendError  # noqa: E402
from cowcraft_backend import CowCraftBackend  # noqa: E402
from cowcraft_mcp import (  # noqa: E402
    DEFAULT_TOKEN_PATH,
    CowCraft,
    CowCraftError,
    account_busy_wait_seconds,
    account_state_paths,
    load_connection_token,
    save_connection_token,
)

GATEWAY_DIR = os.path.dirname(os.path.abspath(__file__))
harness.install_production_guard()


class LeaseRecoveryTest(unittest.TestCase):
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

    def test_default_token_path_is_the_conn_file(self):
        client = CowCraft(url="http://127.0.0.1:9/mcp")
        self.assertEqual(client.token_path, "/tmp/cowcraft_conn.txt")
        self.assertEqual(DEFAULT_TOKEN_PATH, "/tmp/cowcraft_conn.txt")
        print("LEASE default token path is /tmp/cowcraft_conn.txt")

    def test_later_process_disconnects_saved_token_only(self):
        secret = harness.fixture_secret()
        client = self.client("conn.txt", "model.txt")
        token = client.connect("Juno-Model_1")
        with open(client.token_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(text.strip(), token)
        self.assertNotIn(secret, text)
        self.assertNotIn("password", text.lower())
        client.register("leaseuser", secret)
        client.login("leaseuser", secret)
        self.assertIsNotNone(gateway.leases.get_lease(token)[0])
        print("LEASE token file matches issued token and has no password")

        del client
        env = os.environ.copy()
        env["GATEWAY_DIR"] = GATEWAY_DIR
        env["COWCRAFT_URL"] = self.url
        env["COWCRAFT_TOKEN_PATH"] = self.path("conn.txt")
        env["COWCRAFT_MODEL_PATH"] = self.path("child-model.txt")
        env["BACKEND"] = "stub"
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os, sys\n"
             "sys.path.insert(0, os.environ['GATEWAY_DIR'])\n"
             "from cowcraft_mcp import CowCraft\n"
             "client = CowCraft(url=os.environ['COWCRAFT_URL'],"
             " token_path=os.environ['COWCRAFT_TOKEN_PATH'],"
             " model_path=os.environ['COWCRAFT_MODEL_PATH'])\n"
             "assert client.connection is None\n"
             "client.disconnect()\n"
             "print('released')\n"],
            cwd=GATEWAY_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("released", proc.stdout)
        self.assertIsNone(gateway.leases.get_lease(token)[0])
        print("LEASE later process released the saved token")

        again = self.client("conn-2.txt", "model-2.txt")
        again.connect("Juno-Model_1")
        again.login("leaseuser", secret)
        print("LEASE account accepts a login after the saved token was released")

    def test_fresh_token_does_not_clear_saved_lease(self):
        secret = harness.fixture_secret()
        holder = self.client("conn.txt", "model.txt")
        saved = holder.connect("Juno-Model_1")
        holder.register("holduser", secret)
        holder.login("holduser", secret)

        fresh = self.client("conn.txt", "model.txt")
        issued = fresh.connect("Juno-Model_1")
        self.assertNotEqual(issued, saved)
        with open(holder.token_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), saved)
        fresh.disconnect()
        self.assertIsNotNone(gateway.leases.get_lease(saved)[0])
        self.assertIsNone(gateway.leases.get_lease(issued)[0])
        with open(holder.token_path, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), saved)

        other = self.client("other.txt", "other-model.txt")
        other.connect("Juno-Model_1")
        with self.assertRaises(CowCraftError) as caught:
            other.login("holduser", secret)
        self.assertEqual(caught.exception.code, "account_busy")
        self.assertIsNotNone(gateway.leases.get_lease(saved)[0])
        print("LEASE fresh token did not release the saved lease")

    def test_account_busy_disconnects_saved_token_then_waits(self):
        secret = harness.fixture_secret()
        holder = self.client("conn.txt", "model.txt")
        saved = holder.connect("Juno-Model_1")
        holder.register("busyuser", secret)
        holder.login("busyuser", secret)

        clock = harness.ManualClock()
        fresh = self.client("conn.txt", "model-fresh.txt", clock=clock)
        issued = fresh.connect("Juno-Model_1")
        self.assertNotEqual(issued, saved)
        with self.assertRaises(CowCraftError) as caught:
            fresh.login("busyuser", secret)
        self.assertEqual(caught.exception.code, "account_busy")
        self.assertNotEqual(caught.exception.code, "rate_limited")
        self.assertIn(account_busy_wait_seconds(), clock.sleeps)
        self.assertTrue(all(delay < 60 for delay in clock.sleeps))
        self.assertIsNone(gateway.leases.get_lease(saved)[0])
        self.assertIsNotNone(gateway.leases.get_lease(issued)[0])
        print("LEASE account_busy disconnected the saved token and waited")

        # The busy result did not start the 60s login clock.
        fresh.login("busyuser", secret)
        self.assertEqual(
            gateway.leases.get_lease(issued)[0].account,
            "busyuser",
        )
        print("LEASE account_busy did not start the login-retry clock")

    def test_dropped_disconnect_keeps_the_token_for_a_later_release(self):
        secret = harness.fixture_secret()
        clock = harness.ManualClock()
        client = self.client("conn.txt", "model.txt", clock=clock)
        token = client.connect("Juno-Model_1")
        client.register("dropuser", secret)
        client.login("dropuser", secret)
        self.assertEqual(load_connection_token(client.token_path), token)
        self.assertIsNotNone(gateway.leases.get_lease(token)[0])

        real = client._transport
        drops = {"n": 0}

        def fail_disconnect(url, payload, headers, timeout):
            if payload.get("params", {}).get("name") == "disconnect":
                drops["n"] += 1
                raise IncompleteRead(b"")
            return real(url, payload, headers, timeout)

        client._transport = fail_disconnect
        result = client.disconnect()
        self.assertEqual(result, {"disconnected": False})
        self.assertEqual(drops["n"], 3)
        self.assertEqual(client.connection, token)
        self.assertEqual(load_connection_token(client.token_path), token)
        self.assertIsNotNone(gateway.leases.get_lease(token)[0])
        self.assertEqual(clock.sleeps, [0.5, 1.0])
        print("LEASE three IncompleteReads left the saved token in place")

        later = self.client("conn.txt", "later-model.txt")
        self.assertIsNone(later.connection)
        released = later.disconnect()
        self.assertTrue(released.get("disconnected"))
        self.assertIsNone(load_connection_token(client.token_path))
        self.assertIsNone(gateway.leases.get_lease(token)[0])
        print("LEASE a later disconnect of that same token released the lease")

    def test_login_backs_up_replaced_token_for_a_later_release(self):
        secret = harness.fixture_secret()
        first = self.client("conn.txt", "model.txt")
        saved = first.connect("Field-Scout 1")
        second = self.client("conn.txt", "model.txt")
        issued = second.connect("Field-Scout 1")
        self.assertNotEqual(issued, saved)
        self.assertEqual(load_connection_token(first.token_path), saved)
        second.register("bakuser", secret)
        second.login("bakuser", secret)
        self.assertEqual(load_connection_token(first.token_path), issued)
        backup = first.token_path + ".bak"
        self.assertEqual(load_connection_token(backup), saved)
        with open(backup, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(secret, text)
        self.assertNotIn("password", text.lower())
        self.assertIsNotNone(gateway.leases.get_lease(saved)[0])
        self.assertIsNotNone(gateway.leases.get_lease(issued)[0])

        env = os.environ.copy()
        env["GATEWAY_DIR"] = GATEWAY_DIR
        env["COWCRAFT_URL"] = self.url
        env["COWCRAFT_TOKEN_PATH"] = backup
        env["COWCRAFT_MODEL_PATH"] = self.path("backup-model.txt")
        env["BACKEND"] = "stub"
        proc = subprocess.run(
            [sys.executable, "-c",
             "import os, sys\n"
             "sys.path.insert(0, os.environ['GATEWAY_DIR'])\n"
             "from cowcraft_mcp import CowCraft\n"
             "client = CowCraft(url=os.environ['COWCRAFT_URL'],"
             " token_path=os.environ['COWCRAFT_TOKEN_PATH'],"
             " model_path=os.environ['COWCRAFT_MODEL_PATH'])\n"
             "assert client.connection is None\n"
             "result = client.disconnect()\n"
             "assert result.get('disconnected') is True\n"
             "print('released-backup')\n"],
            cwd=GATEWAY_DIR,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("released-backup", proc.stdout)
        self.assertNotIn(secret, proc.stdout)
        self.assertIsNone(gateway.leases.get_lease(saved)[0])
        self.assertIsNotNone(gateway.leases.get_lease(issued)[0])
        print("LEASE login backed up the replaced token and a later process released it")


class ProxyLeaseTest(unittest.TestCase):
    def test_proxy_disconnects_persisted_token_not_the_new_one(self):
        secret = harness.fixture_secret()
        saved = "saved-lease-token"
        issued = "fresh-issued-token"
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            model_path = os.path.join(tmp, "model.txt")
            save_connection_token(token_path, saved)
            transport = harness.FakeTransport([
                harness.tool_result({"disconnected": True}),
                harness.rpc_result({"protocolVersion": "2024-11-05"}),
                harness.tool_result({"connection": issued}),
                harness.tool_result({"username": "ada"}),
            ])

            def factory(username=None):
                return CowCraft(
                    url="http://127.0.0.1:9/mcp",
                    token_path=token_path,
                    model_path=model_path,
                    transport=transport,
                )

            backend = CowCraftBackend(client_factory=factory)
            result = backend.login("ada", secret)
            self.assertEqual(result["username"], "ada")
            disconnected = harness.named(transport.payloads, "disconnect")
            self.assertEqual(len(disconnected), 1)
            self.assertEqual(
                disconnected[0]["params"]["arguments"]["connection"],
                saved,
            )
            self.assertNotEqual(
                disconnected[0]["params"]["arguments"]["connection"],
                issued,
            )
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
            print("LEASE proxy disconnected the persisted token, not the new one")

    def test_proxy_account_busy_is_not_rate_limited(self):
        secret = harness.fixture_secret()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            model_path = os.path.join(tmp, "model.txt")
            clock = harness.ManualClock()
            transport = harness.FakeTransport([
                harness.rpc_result({"protocolVersion": "2024-11-05"}),
                harness.tool_result({"connection": "fresh-issued-token"}),
                harness.tool_result({
                    "error": {
                        "code": "account_busy",
                        "message": "This account already has a direct agent lease",
                    },
                }),
            ])

            def factory(username=None):
                return CowCraft(
                    url="http://127.0.0.1:9/mcp",
                    token_path=token_path,
                    model_path=model_path,
                    transport=transport,
                    clock=clock,
                    sleep=clock.sleep,
                )

            backend = CowCraftBackend(client_factory=factory)
            with self.assertRaises(BackendError) as caught:
                backend.login("ada", secret)
            self.assertEqual(caught.exception.code, "account_busy")
            self.assertNotEqual(caught.exception.code, "rate_limited")
            self.assertEqual(harness.named(transport.payloads, "disconnect"), [])
            self.assertIn(account_busy_wait_seconds(), clock.sleeps)
            self.assertNotIn("password", caught.exception.message)
            print("LEASE proxy surfaced account_busy without dropping the fresh token")

    def _proxy(self, script, token_path, model_path, clock=None):
        transport = harness.FakeTransport(script)
        kwargs = {"transport": transport}
        if clock is not None:
            kwargs["clock"] = clock
            kwargs["sleep"] = clock.sleep

        def factory(username=None):
            return CowCraft(
                url="http://127.0.0.1:9/mcp",
                token_path=token_path,
                model_path=model_path,
                **kwargs,
            )

        return CowCraftBackend(client_factory=factory), transport

    def test_proxy_returns_upstream_login_body_for_each_caller(self):
        for username, password in (("ada", "n" * 12), ("bao", "m" * 12)):
            upstream = {
                "username": username,
                "state": "character_selection",
                "lease": {"state": "character_selection"},
            }
            with tempfile.TemporaryDirectory() as tmp:
                backend, transport = self._proxy(
                    [
                        harness.rpc_result({"protocolVersion": "2024-11-05"}),
                        harness.tool_result({"connection": "issued-" + username}),
                        harness.tool_result(upstream),
                    ],
                    os.path.join(tmp, "conn.txt"),
                    os.path.join(tmp, "model.txt"),
                )
                result = backend.login(username, password)
                self.assertEqual(result, upstream)
                sent = harness.named(transport.payloads, "login")
                self.assertEqual(len(sent), 1)
                args = sent[0]["params"]["arguments"]
                self.assertEqual(args["username"], username)
                self.assertEqual(args["password"], password)
                self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
                with open(os.path.join(tmp, "conn.txt"), encoding="utf-8") as handle:
                    text = handle.read()
                self.assertNotIn(password, text)
        print("PROXY login returned the upstream session body for each caller")

    def test_proxy_sends_caller_agent_label_unchanged(self):
        password = harness.fixture_secret()
        label = "Field-Scout 1"
        with tempfile.TemporaryDirectory() as tmp:
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": "issued-ada"}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                ],
                os.path.join(tmp, "conn.txt"),
                os.path.join(tmp, "model.txt"),
            )
            backend.login("ada", password, agent_label=label)
            sent = harness.named(transport.payloads, "connect")
            self.assertEqual(len(sent), 1)
            self.assertEqual(sent[0]["params"]["arguments"]["model_name"], label)
            self.assertNotEqual(sent[0]["params"]["arguments"]["model_name"], "minta-proxy-ada")
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY connect sent the caller agent label unchanged")

    def test_proxy_transport_retry_does_not_login_again_or_drop_token(self):
        password = harness.fixture_secret()
        issued = "fresh-issued-token"
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                    IncompleteRead(b""),
                    IncompleteRead(b""),
                    IncompleteRead(b""),
                    harness.tool_result({"self": {"money": {"copper": 1}}}),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
                clock=clock,
            )
            backend.login("ada", password)
            self.assertEqual(load_connection_token(token_path), issued)
            observed = backend.observe("ada")
            self.assertEqual(observed["self"]["money"]["copper"], 1)
            self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
            self.assertEqual(len(harness.named(transport.payloads, "connect")), 1)
            self.assertEqual(harness.named(transport.payloads, "disconnect"), [])
            self.assertEqual(load_connection_token(token_path), issued)
            with open(token_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(password, text)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
            self.assertTrue(all(delay < 60 for delay in clock.sleeps))
        print("PROXY transport retry sent no second login and kept the saved token")

    def test_proxy_busy_login_leaves_other_account_token(self):
        password = "m" * 12
        ada_token = "ada-saved-lease-token"
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            save_connection_token(token_path, ada_token)
            with open(token_path + ".owner", "w", encoding="utf-8") as handle:
                handle.write("ada\n")
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": "bao-fresh-token"}),
                    harness.tool_result({
                        "error": {
                            "code": "account_busy",
                            "message": "This account already has a direct agent lease",
                        },
                    }),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
                clock=clock,
            )
            with self.assertRaises(BackendError) as caught:
                backend.login("bao", password)
            self.assertEqual(caught.exception.code, "account_busy")
            self.assertNotEqual(caught.exception.code, "rate_limited")
            self.assertEqual(load_connection_token(token_path), ada_token)
            self.assertEqual(harness.named(transport.payloads, "disconnect"), [])
            self.assertIn(account_busy_wait_seconds(), clock.sleeps)
            self.assertNotIn(password, caught.exception.message)
            self.assertNotIn(ada_token, caught.exception.message)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY account_busy left the other account token in place")

    def test_proxy_keeps_saved_token_when_disconnect_does_not_land(self):
        password = harness.fixture_secret()
        saved = "saved-lease-token"
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            save_connection_token(token_path, saved)
            backend, transport = self._proxy(
                [
                    IncompleteRead(b""),
                    IncompleteRead(b""),
                    IncompleteRead(b""),
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": "should-not-issue"}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
                clock=clock,
            )
            with self.assertRaises(BackendError) as caught:
                backend.login("ada", password)
            self.assertNotEqual(caught.exception.code, "rate_limited")
            self.assertEqual(load_connection_token(token_path), saved)
            self.assertEqual(harness.named(transport.payloads, "login"), [])
            self.assertEqual(harness.named(transport.payloads, "connect"), [])
            with open(token_path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(password, text)
            self.assertNotIn(password, caught.exception.message)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY kept the saved token when disconnect did not land")

    def test_proxy_accounts_do_not_share_a_token_file(self):
        password = "m" * 12
        ada_token = "ada-saved-lease-token"
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            ada_path, _ada_model = account_state_paths(tmp, "ada")
            save_connection_token(ada_path, ada_token)
            with open(ada_path + ".owner", "w", encoding="utf-8") as handle:
                handle.write("ada\n")
            transport = harness.FakeTransport([
                harness.rpc_result({"protocolVersion": "2024-11-05"}),
                harness.tool_result({"connection": "bao-fresh-token"}),
                harness.tool_result({
                    "error": {
                        "code": "account_busy",
                        "message": "lease held",
                    },
                }),
            ])

            def factory(username):
                token_path, model_path = account_state_paths(tmp, username)
                return CowCraft(
                    url="http://127.0.0.1:9/mcp",
                    token_path=token_path,
                    model_path=model_path,
                    transport=transport,
                    clock=clock,
                    sleep=clock.sleep,
                )

            backend = CowCraftBackend(client_factory=factory)
            with self.assertRaises(BackendError) as caught:
                backend.login("bao", password)
            self.assertEqual(caught.exception.code, "account_busy")
            self.assertNotEqual(caught.exception.code, "rate_limited")
            self.assertEqual(load_connection_token(ada_path), ada_token)
            self.assertEqual(harness.named(transport.payloads, "disconnect"), [])
            bao_path, _bao_model = account_state_paths(tmp, "bao")
            self.assertNotEqual(os.path.abspath(ada_path), os.path.abspath(bao_path))
            self.assertIn(account_busy_wait_seconds(), clock.sleeps)
            self.assertNotIn(password, caught.exception.message)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY one account busy left the other account token file alone")

    def test_proxy_backs_up_saved_token_before_replace(self):
        password = harness.fixture_secret()
        saved = "saved-lease-token"
        issued = "fresh-issued-token"
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            save_connection_token(token_path, saved)
            backend, transport = self._proxy(
                [
                    harness.tool_result({"disconnected": True}),
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
            )
            result = backend.login("ada", password)
            self.assertEqual(result["username"], "ada")
            self.assertEqual(result["state"], "character_selection")
            self.assertEqual(load_connection_token(token_path), issued)
            backup = token_path + ".bak"
            self.assertEqual(load_connection_token(backup), saved)
            with open(backup, encoding="utf-8") as handle:
                text = handle.read()
            self.assertNotIn(password, text)
            self.assertNotIn("password", text.lower())
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY backed up the saved token before replacing it")

    def test_proxy_forwards_spell_limit_and_ui_sections(self):
        password = harness.fixture_secret()
        issued = "spell-observe-token"
        with tempfile.TemporaryDirectory() as tmp:
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                    harness.tool_result({
                        "spells": [{
                            "spell_id": 78,
                            "name": "Heroic Strike",
                            "power_cost": 15,
                        }],
                    }),
                ],
                os.path.join(tmp, "conn.txt"),
                os.path.join(tmp, "model.txt"),
            )
            backend.login("ada", password, agent_label="Field-Scout 1")
            observed = backend.observe(
                "ada", sections=["self", "ui"], spell_limit=40)
            self.assertEqual(observed["spells"][0]["name"], "Heroic Strike")
            calls = harness.named(transport.payloads, "observe")
            self.assertEqual(len(calls), 1)
            arguments = calls[0]["params"]["arguments"]
            self.assertEqual(arguments["spell_limit"], 40)
            self.assertEqual(arguments["sections"], ["self", "ui"])
            self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
            self.assertNotIn(password, str(arguments))
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY observe forwarded spell_limit and the ui section")

    def test_proxy_login_twice_inside_ten_seconds_keeps_the_session(self):
        password = harness.fixture_secret()
        issued = "fresh-issued-token"
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                        "lease": {"state": "character_selection"},
                    }),
                    harness.tool_result({"self": {"money": {"copper": 4}}}),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
                clock=clock,
            )
            first = backend.login("ada", password, agent_label="ada")
            second = backend.login("ada", password, agent_label="ada")
            observed = backend.observe("ada")
            self.assertEqual(first["state"], "character_selection")
            self.assertEqual(second["state"], "character_selection")
            self.assertNotEqual(getattr(second, "code", None), "rate_limited")
            self.assertEqual(observed["self"]["money"]["copper"], 4)
            self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
            self.assertEqual(load_connection_token(token_path), issued)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY a second login inside 10s left the session usable")

    def test_proxy_busy_retry_sends_login_inside_ten_seconds(self):
        password = harness.fixture_secret()
        clock = harness.ManualClock()
        with tempfile.TemporaryDirectory() as tmp:
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": "busy-token"}),
                    harness.tool_result({
                        "error": {
                            "code": "account_busy",
                            "message": "This account already has a direct agent lease",
                        },
                    }),
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": "retry-token"}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                        "lease": {"state": "character_selection"},
                    }),
                ],
                os.path.join(tmp, "conn.txt"),
                os.path.join(tmp, "model.txt"),
                clock=clock,
            )
            with self.assertRaises(BackendError) as caught:
                backend.login("ada", password, agent_label="ada")
            self.assertEqual(caught.exception.code, "account_busy")
            self.assertNotEqual(caught.exception.code, "rate_limited")
            body = backend.login("ada", password, agent_label="ada")
            self.assertEqual(body["state"], "character_selection")
            self.assertNotEqual(getattr(body, "code", None), "rate_limited")
            self.assertEqual(len(harness.named(transport.payloads, "login")), 2)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
        print("PROXY account_busy retry sent the login inside 10 seconds")

    def test_proxy_leave_world_reports_upstream_disconnect(self):
        password = harness.fixture_secret()
        issued = "leave-token"
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            backend, transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                    harness.tool_result({"disconnected": False}),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
            )
            backend.login("ada", password)
            failed = backend.leave_world("ada")
            self.assertIs(failed.get("disconnected"), False)
            self.assertIs(failed.get("left"), False)
            self.assertEqual(load_connection_token(token_path), issued)
            self.assertEqual(len(harness.named(transport.payloads, "disconnect")), 1)

        issued = "leave-token-ok"
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            backend, _transport = self._proxy(
                [
                    harness.rpc_result({"protocolVersion": "2024-11-05"}),
                    harness.tool_result({"connection": issued}),
                    harness.tool_result({
                        "username": "ada",
                        "state": "character_selection",
                    }),
                    harness.tool_result({"disconnected": True}),
                ],
                token_path,
                os.path.join(tmp, "model.txt"),
            )
            backend.login("ada", password)
            released = backend.leave_world("ada")
            self.assertIs(released.get("disconnected"), True)
            self.assertIs(released.get("left"), True)
            self.assertIsNone(load_connection_token(token_path))
        print("PROXY leave_world returned the upstream disconnect result")


if __name__ == "__main__":
    unittest.main(verbosity=2)
