"""Helpers for the CowCraft gateway tests. Not a second client."""

import json
import os
import socket
import threading
import time
import urllib.request

os.environ["BACKEND"] = "stub"

import backend as backend_mod  # noqa: E402
import gateway  # noqa: E402


def install_production_guard():
    """Fail any test that opens a socket to the live CowCraft host."""
    real_create = socket.create_connection
    real_dns = socket.getaddrinfo

    def create_connection(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host and "cowcraft.world" in str(host):
            raise AssertionError("refusing socket to cowcraft.world")
        return real_create(address, *args, **kwargs)

    def getaddrinfo(host, *args, **kwargs):
        if host and "cowcraft.world" in str(host):
            raise AssertionError("refusing DNS for cowcraft.world")
        return real_dns(host, *args, **kwargs)

    socket.create_connection = create_connection
    socket.getaddrinfo = getaddrinfo


class ManualClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        seconds = float(seconds)
        self.sleeps.append(seconds)
        self.now += seconds


def rpc_result(result):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})


def tool_result(payload):
    return rpc_result({
        "content": [{"type": "text", "text": json.dumps(payload)}],
    })


class FakeTransport:
    def __init__(self, script):
        self.script = list(script)
        self.payloads = []
        self.urls = []

    def __call__(self, url, payload, headers, timeout):
        if "cowcraft.world" in str(url):
            raise AssertionError("contacted cowcraft.world")
        self.urls.append(url)
        self.payloads.append(payload)
        if not self.script:
            raise AssertionError("unexpected request")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def fixture_secret():
    return "n" * 12


def named(payloads, name):
    return [
        item for item in payloads
        if item.get("params", {}).get("name") == name
    ]


def reset_gateway():
    with gateway.leases._lock:
        gateway.leases._leases.clear()
        gateway.leases._account_lease.clear()
    with gateway._agent_binds_lock:
        gateway._agent_binds.clear()
    gateway.backend = backend_mod.StubBackend()


def start_gateway():
    reset_gateway()
    server = gateway.ThreadedHTTPServer(("127.0.0.1", 0), gateway.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    health = "http://127.0.0.1:%d/health" % port
    url = "http://127.0.0.1:%d/mcp" % port
    last = None
    for _ in range(50):
        try:
            with urllib.request.urlopen(health, timeout=1) as resp:
                body = json.loads(resp.read().decode())
            if body.get("ok") is True and body.get("backend") == "stub":
                return server, url
        except Exception as exc:
            last = exc
            time.sleep(0.05)
    raise RuntimeError("stub gateway did not start: %s" % last)
