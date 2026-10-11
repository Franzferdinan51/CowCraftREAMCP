"""Transport, observe slot, and login clocks on the shipped client.

The fake transport never opens a socket. A clock is injected so the 60s
login gate does not sleep. Three simulated failure iterations must pass,
and three failures before a later success stop.
"""

import os
import sys
import tempfile
import unittest
from http.client import IncompleteRead, RemoteDisconnected

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["BACKEND"] = "stub"

import harness  # noqa: E402
from cowcraft_mcp import (  # noqa: E402
    CowCraft,
    CowCraftError,
    account_busy_wait_seconds,
    save_connection_token,
    transport_backoff_seconds,
)

harness.install_production_guard()
LOCAL = "http://127.0.0.1:9/mcp"


def success_body():
    return harness.rpc_result({"protocolVersion": "2024-11-05", "ok": True})


class TransportPolicyTest(unittest.TestCase):
    def _client(self, script, clock=None, token_path=None, model_path=None):
        clock = clock or harness.ManualClock()
        transport = harness.FakeTransport(script)
        client = CowCraft(
            url=LOCAL,
            token_path=token_path,
            model_path=model_path,
            transport=transport,
            clock=clock,
            sleep=clock.sleep,
        )
        return client, transport, clock

    def test_three_failure_iterations_then_success(self):
        sequences = [
            [IncompleteRead(b""), success_body()],
            [RemoteDisconnected(), success_body()],
            [IncompleteRead(b""), RemoteDisconnected(), success_body()],
        ]
        for index, script in enumerate(sequences, start=1):
            client, transport, clock = self._client(script)
            result = client.initialize()
            self.assertEqual(result["protocolVersion"], "2024-11-05")
            self.assertEqual(len(transport.script), 0)
            self.assertGreaterEqual(len(clock.sleeps), 1)
            for earlier, later in zip(clock.sleeps, clock.sleeps[1:]):
                self.assertLess(earlier, later)
            self.assertEqual(clock.sleeps[0], transport_backoff_seconds(0))
            if len(clock.sleeps) > 1:
                self.assertEqual(clock.sleeps[1], transport_backoff_seconds(1))
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
            print("TRANSPORT iteration %d recovered after %d drop(s)" % (
                index, len(clock.sleeps)))

    def test_three_failures_stop_without_another_attempt(self):
        sentinel = success_body()
        client, transport, clock = self._client([
            IncompleteRead(b""),
            RemoteDisconnected(),
            IncompleteRead(b"partial"),
            sentinel,
        ])
        with self.assertRaises(CowCraftError) as caught:
            client.initialize()
        self.assertEqual(caught.exception.code, "transport")
        self.assertEqual(len(transport.payloads), 3)
        self.assertEqual(transport.script, [sentinel])
        self.assertEqual(clock.sleeps, [
            transport_backoff_seconds(0),
            transport_backoff_seconds(1),
        ])
        self.assertLess(clock.sleeps[0], clock.sleeps[1])
        print("TRANSPORT three failures stopped before the later success")

    def test_observe_and_heartbeat_share_one_per_second(self):
        body = harness.tool_result({"self": {"name": "Theron"}, "lease": {}})
        times = []
        clock = harness.ManualClock()

        class Recording(harness.FakeTransport):
            def __call__(self, url, payload, headers, timeout):
                times.append(clock())
                return super().__call__(url, payload, headers, timeout)

        transport = Recording([body, body, body])
        client = CowCraft(
            url=LOCAL,
            transport=transport,
            clock=clock,
            sleep=clock.sleep,
        )
        client.connection = "held"
        client.observe()
        client.heartbeat()
        client.observe()
        names = [item["params"]["name"] for item in transport.payloads]
        self.assertEqual(names, ["observe", "heartbeat", "observe"])
        self.assertEqual(times, [0.0, 1.0, 2.0])
        self.assertGreaterEqual(times[1] - times[0], 1.0)
        self.assertGreaterEqual(times[2] - times[1], 1.0)
        self.assertEqual(clock.sleeps, [1.0, 1.0])
        print("TRANSPORT observe and heartbeat stayed at one call per second")

    def test_second_login_inside_60s_is_not_sent(self):
        client, transport, clock = self._client([
            harness.tool_result({"username": "ada"}),
            harness.tool_result({"username": "ada"}),
        ])
        client.connection = "held"
        secret = harness.fixture_secret()
        client.login("ada", secret)
        with self.assertRaises(CowCraftError) as caught:
            client.login("ada", secret)
        self.assertEqual(caught.exception.code, "login_suppressed")
        self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
        self.assertEqual(clock.sleeps, [])
        print("TRANSPORT second login inside 60s was not sent")

    def test_dropped_login_is_not_resent(self):
        client, transport, _clock = self._client([
            IncompleteRead(b""),
            harness.tool_result({"username": "ada"}),
        ])
        client.connection = "held"
        with self.assertRaises(CowCraftError) as caught:
            client.login("ada", harness.fixture_secret())
        self.assertEqual(caught.exception.code, "transport")
        self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
        self.assertEqual(len(transport.script), 1)
        with self.assertRaises(CowCraftError) as suppressed:
            client.login("ada", harness.fixture_secret())
        self.assertEqual(suppressed.exception.code, "login_suppressed")
        self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
        print("TRANSPORT a dropped login was not retried inside 60s")

    def test_rate_limited_waits_longer_than_transport_retry(self):
        client, transport, clock = self._client([
            harness.tool_result({
                "error": {"code": "rate_limited", "message": "slow down"},
                "retry_after_ms": 5000,
            }),
        ])
        client.connection = "held"
        with self.assertRaises(CowCraftError) as caught:
            client.login("ada", harness.fixture_secret())
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertGreaterEqual(clock.sleeps[0], 5.0)
        self.assertGreater(clock.sleeps[0], transport_backoff_seconds(0))
        self.assertEqual(len(harness.named(transport.payloads, "login")), 1)
        print("TRANSPORT rate_limited waited %ss" % clock.sleeps[0])

    def test_account_busy_is_not_on_the_login_clock(self):
        with tempfile.TemporaryDirectory() as tmp:
            token_path = os.path.join(tmp, "conn.txt")
            model_path = os.path.join(tmp, "model.txt")
            save_connection_token(token_path, "saved-lease-token")
            client, transport, clock = self._client(
                [
                    harness.tool_result({
                        "error": {
                            "code": "account_busy",
                            "message": "lease held",
                        },
                    }),
                    harness.tool_result({"disconnected": True}),
                    harness.tool_result({"username": "ada"}),
                ],
                token_path=token_path,
                model_path=model_path,
            )
            client.connection = "fresh-issued-token"
            with self.assertRaises(CowCraftError) as caught:
                client.login("ada", harness.fixture_secret())
            self.assertEqual(caught.exception.code, "account_busy")
            self.assertNotEqual(caught.exception.code, "rate_limited")
            disconnected = harness.named(transport.payloads, "disconnect")
            self.assertEqual(
                disconnected[0]["params"]["arguments"]["connection"],
                "saved-lease-token",
            )
            self.assertNotIn(
                "fresh-issued-token",
                [item["params"]["arguments"]["connection"] for item in disconnected],
            )
            self.assertIn(account_busy_wait_seconds(), clock.sleeps)
            self.assertLess(
                account_busy_wait_seconds(),
                transport_backoff_seconds(0),
            )
            client.login("ada", harness.fixture_secret())
            self.assertEqual(len(harness.named(transport.payloads, "login")), 2)
            self.assertTrue(all("cowcraft.world" not in url for url in transport.urls))
            print("TRANSPORT account_busy was not on the login-retry clock")


if __name__ == "__main__":
    unittest.main(verbosity=2)
