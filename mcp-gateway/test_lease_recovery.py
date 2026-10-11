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

            def factory():
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

            def factory():
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
