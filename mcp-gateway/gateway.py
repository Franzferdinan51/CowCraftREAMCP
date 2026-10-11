#!/usr/bin/env python3
"""
Minta MCP gateway — a CowCraft-compatible MCP server for vanilla WoW.

Replicates cowcraft.world/mcp's protocol:
- Streamable HTTP: one JSON-RPC message per POST, plain JSON response
- MCP methods: initialize, ping, tools/list, tools/call
- 13 tools, 155 game action capabilities
- Token leases (UUIDv4.HMAC-SHA256), per-account serial, rate limits

Backend: configurable via BACKEND env var.
    stub  -> in-memory fake world (default; for protocol development)
    live  -> import live_backend.WoWBackendImpl (you write this against CMaNGOS)

Usage:
    python3 gateway.py [--host 127.0.0.1] [--port 8787]

Then point an MCP client at http://host:port/mcp
"""

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from leases import LeaseManager, LeaseState          # noqa: E402
from tools import TOOLS, tool_names                 # noqa: E402
from actions import CAPABILITIES, validate_action    # noqa: E402
from backend import StubBackend, BackendError        # noqa: E402

SERVER_NAME = "minta"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2024-11-05"

INSTRUCTIONS = (
    "Minta MCP gateway for vanilla WoW (1.12.1). One lease per account; "
    "observe/heartbeat share a 1/sec budget; actions max 20/sec. Honor "
    "retry_after_ms; never busy-loop. Agents cannot use Player Tools, "
    "instant levelling, map teleport, custom mounts, chat, or human-written "
    "mail. First MCP entry binds the character as agent-bound permanently."
)


def get_backend():
    mode = os.environ.get("BACKEND", "stub")
    if mode == "live":
        from live_backend import WoWBackendImpl
        return WoWBackendImpl()
    return StubBackend()


leases = LeaseManager()
backend = get_backend()
# per-account serial lock: one in-flight request per account at a time
_account_locks = {}
_account_locks_guard = threading.Lock()


def account_lock(username):
    with _account_locks_guard:
        lock = _account_locks.get(username.lower())
        if lock is None:
            lock = threading.Lock()
            _account_locks[username.lower()] = lock
        return lock


# --------------------------------------------------------------------------
# MCP envelope helpers
# --------------------------------------------------------------------------

def mcp_text(payload):
    """Wrap a result dict in the MCP content[0].text envelope."""
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def game_error(code, message, **extra):
    payload = {"error": {"code": code, "message": message}}
    payload.update(extra)
    return mcp_text(payload)


def ok_result(payload):
    return mcp_text(payload)


# --------------------------------------------------------------------------
# Tool implementations
# --------------------------------------------------------------------------

def tool_connect(args):
    model_name = args.get("model_name")
    if not model_name or not isinstance(model_name, str):
        return game_error("invalid_arguments", "model_name is required")
    lease = leases.create_lease(model_name)
    lease.touch()
    return ok_result({
        "connection": lease.token,
        "lease": {"seconds_remaining": 600,
                  "state": lease.state,
                  "idle_timeout": 600},
        "transport": "direct-server",
        "capabilities": CAPABILITIES,
        "capabilities_confirmed": True,
        "instructions": INSTRUCTIONS,
    })


def _authed(args):
    """Resolve lease from args['connection']. Returns (lease, error_result)."""
    token = args.get("connection")
    lease, err = leases.get_lease(token)
    if err == "invalid_connection":
        return None, game_error("invalid_connection",
                                "unknown or malformed connection token")
    if err == "lease_expired":
        return None, game_error("lease_expired",
                                "lease expired; reconnect and re-login")
    return lease, None


def tool_register(args):
    lease, err = _authed(args)
    if err:
        return err
    username = args.get("username", "")
    password = args.get("password", "")
    try:
        with account_lock(username or "register"):
            result = backend.register(username, password)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_login(args):
    lease, err = _authed(args)
    if err:
        return err
    username = args.get("username", "")
    password = args.get("password", "")
    try:
        with account_lock(username or "login"):
            result = backend.login(username, password)
    except BackendError as e:
        lease.touch()
        retry = {"retry_after_ms": 5000} if e.code == "rate_limited" else {}
        return game_error(e.code, e.message, **retry)
    busy = leases.bind_account(lease, username)
    if busy:
        return game_error(
            "account_busy",
            "This account already has a direct agent lease; "
            "disconnect the existing session first.")
    return ok_result(result)


def tool_characters(args):
    lease, err = _authed(args)
    if err:
        return err
    if not lease.account:
        lease.touch()
        return game_error("not_authenticated", "login first")
    try:
        result = backend.list_characters(lease.account)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_create_character(args):
    lease, err = _authed(args)
    if err:
        return err
    if not lease.account:
        lease.touch()
        return game_error("not_authenticated", "login first")
    character = args.get("character") or {}
    try:
        with account_lock(lease.account):
            result = backend.create_character(lease.account, character)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_delete_character(args):
    lease, err = _authed(args)
    if err:
        return err
    if not lease.account:
        lease.touch()
        return game_error("not_authenticated", "login first")
    guid = args.get("guid")
    confirm_name = args.get("confirm_name", "")
    if not isinstance(guid, int):
        lease.touch()
        return game_error("invalid_arguments",
                          "guid must be a JSON number")
    try:
        with account_lock(lease.account):
            result = backend.delete_character(lease.account, guid,
                                              confirm_name)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_select_character(args):
    lease, err = _authed(args)
    if err:
        return err
    if not lease.account:
        lease.touch()
        return game_error("not_authenticated", "login first")
    guid = args.get("guid")
    if not isinstance(guid, int):
        lease.touch()
        return game_error("invalid_arguments",
                          "guid must be a JSON number, not a string")
    try:
        with account_lock(lease.account):
            result = backend.enter_world(lease.account, guid)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    leases.bind_character(lease, guid)
    return ok_result(result)


def tool_observe(args):
    lease, err = _authed(args)
    if err:
        return err
    if lease.state != LeaseState.IN_WORLD:
        lease.touch()
        return game_error("not_in_world", "select a character first")
    wait_ms = lease.check_observe_budget()
    if wait_ms:
        lease.touch()
        return game_error("rate_limited",
                          "observe/heartbeat budget exceeded (1/sec)",
                          retry_after_ms=wait_ms)
    try:
        with account_lock(lease.account):
            result = backend.observe(lease.account,
                                     sections=args.get("sections"))
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    result = dict(result)
    result["lease"] = {"seconds_remaining":
                       max(0, int(lease.expires_at - time.time())),
                       "state": lease.state}
    lease.touch()
    return ok_result(result)


def tool_heartbeat(args):
    lease, err = _authed(args)
    if err:
        return err
    wait_ms = lease.check_observe_budget()
    if wait_ms:
        lease.touch()
        return game_error("rate_limited",
                          "observe/heartbeat budget exceeded (1/sec)",
                          retry_after_ms=wait_ms)
    lease.touch()
    return ok_result({"lease": {
        "seconds_remaining": max(0, int(lease.expires_at - time.time())),
        "state": lease.state}})


def tool_action(args):
    lease, err = _authed(args)
    if err:
        return err
    if lease.state != LeaseState.IN_WORLD:
        lease.touch()
        return game_error("not_in_world", "select a character first")
    action = args.get("action") or {}
    valid, msg = validate_action(action)
    if not valid:
        lease.touch()
        return game_error("invalid_arguments", msg)
    wait_ms = lease.check_action_budget()
    if wait_ms:
        lease.touch()
        return game_error("rate_limited",
                          "action budget exceeded (20/sec)",
                          retry_after_ms=wait_ms)
    try:
        with account_lock(lease.account):
            result = backend.do_action(lease.account, action)
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_list_actions(args):
    lease, err = _authed(args)
    if err:
        return err
    if not lease.account:
        lease.touch()
        return game_error("not_authenticated", "login first")
    try:
        result = backend.list_actions(
            lease.account,
            family=args.get("family"), kind=args.get("kind"))
    except BackendError as e:
        lease.touch()
        return game_error(e.code, e.message)
    lease.touch()
    return ok_result(result)


def tool_stop(args):
    lease, err = _authed(args)
    if err:
        return err
    if lease.account and lease.state == LeaseState.IN_WORLD:
        try:
            with account_lock(lease.account):
                backend.stop(lease.account)
        except BackendError:
            pass
    lease.touch()
    return ok_result({"stopped": True})


def tool_disconnect(args):
    token = args.get("connection")
    lease, _ = leases.get_lease(token) if token else (None, None)
    if lease and lease.account:
        try:
            backend.leave_world(lease.account)
        except BackendError:
            pass
    leases.release(token)
    return ok_result({"disconnected": True})


TOOL_HANDLERS = {
    "connect": tool_connect,
    "register": tool_register,
    "login": tool_login,
    "characters": tool_characters,
    "create_character": tool_create_character,
    "delete_character": tool_delete_character,
    "select_character": tool_select_character,
    "observe": tool_observe,
    "heartbeat": tool_heartbeat,
    "action": tool_action,
    "list_actions": tool_list_actions,
    "stop": tool_stop,
    "disconnect": tool_disconnect,
}


# --------------------------------------------------------------------------
# JSON-RPC dispatch
# --------------------------------------------------------------------------

def dispatch(payload):
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": SERVER_NAME,
                           "version": SERVER_VERSION}}}

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return {"jsonrpc": "2.0", "id": req_id, "error": {
                "code": -32601, "message": f"unknown tool: {name}"}}
        try:
            result = handler(arguments)
        except Exception as e:  # never leak tracebacks over the wire
            result = game_error("internal_error", "gateway error")
            print(f"[gateway] handler {name} raised: {e!r}",
                  file=sys.stderr)
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    return {"jsonrpc": "2.0", "id": req_id, "error": {
        "code": -32601, "message": f"Method not found: {method}"}}


# --------------------------------------------------------------------------
# HTTP server (single-shot POST per message)
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "MintaGateway/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[http] " + fmt % args + "\n")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_json({"ok": True, "server": SERVER_NAME,
                             "version": SERVER_VERSION,
                             "backend": os.environ.get("BACKEND", "stub")})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/") != "/mcp":
            self._send_json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > 1_000_000:
            self._send_json({"error": "bad request"}, 400)
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {
                "code": -32700, "message": "Parse error"}}, 400)
            return
        # CowCraft tolerates SSE Accept but answers plain JSON; we do the same.
        response = dispatch(payload)
        self._send_json(response)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    ap = argparse.ArgumentParser(description="Minta MCP gateway")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    if not os.environ.get("MINTA_HMAC_SECRET"):
        print("[gateway] MINTA_HMAC_SECRET not set; using ephemeral secret "
              "(tokens won't survive restarts)", file=sys.stderr)

    srv = ThreadedHTTPServer((args.host, args.port), Handler)
    print(f"[gateway] {SERVER_NAME} v{SERVER_VERSION} listening on "
          f"http://{args.host}:{args.port}/mcp "
          f"(backend={os.environ.get('BACKEND', 'stub')})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[gateway] shutting down")


if __name__ == "__main__":
    main()
