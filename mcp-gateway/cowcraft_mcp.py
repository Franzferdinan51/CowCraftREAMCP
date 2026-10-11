#!/usr/bin/env python3
"""
CowCraft MCP client — play vanilla WoW via the Model Context Protocol.

CowCraft (https://cowcraft.world) is a private vanilla WoW server with an
MCP server at /mcp. This client talks JSON-RPC to that server, or to the
local stub gateway.

Lease recovery, transport backoff, the observe/heartbeat slot, and the
login clocks are pure functions below. CowCraft calls them; tests inject a
clock, a sleeper, and a transport. The default token file is
/tmp/cowcraft_conn.txt and holds the lease token only — never a password.

A newly issued connection token is not a substitute for the saved one.
account_busy disconnects the saved token (when it differs), waits, and is
not treated as rate_limited. Login is sent at most once per 60 seconds.
IncompleteRead and RemoteDisconnected retry with an increasing backoff, and
three failures stop. observe and heartbeat share one call per second.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.client import IncompleteRead, RemoteDisconnected

MCP_URL = "https://cowcraft.world/mcp"
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_TOKEN_PATH = "/tmp/cowcraft_conn.txt"
DEFAULT_MODEL_PATH = "/tmp/cowcraft_model.txt"

LOGIN_MIN_INTERVAL_S = 60.0
OBSERVE_MIN_INTERVAL_S = 1.0
MAX_TRANSPORT_ATTEMPTS = 3

# WoW vanilla class IDs
CLASSES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
    5: "Priest", 7: "Shaman", 8: "Mage", 9: "Warlock", 11: "Druid",
}

# WoW vanilla race IDs (MCP currently supports Human=1 only)
RACES = {1: "Human"}

_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}


class CowCraftError(Exception):
    def __init__(self, message, code=None, retry_after_ms=None):
        super().__init__(message)
        self.code = code
        self.retry_after_ms = retry_after_ms


# --------------------------------------------------------------------------
# Policy. Pure: no sockets, no globals besides the constants above.
# --------------------------------------------------------------------------

def transport_backoff_seconds(attempt):
    """Backoff before the next transport try. attempt 0, 1, ... increases."""
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    return 0.5 * (2 ** attempt)


def account_busy_wait_seconds():
    """Wait after account_busy. Shorter than a transport retry and not 60s."""
    return 0.05


def rate_limit_wait_seconds(retry_after_ms, transport_retry_seconds):
    """Honor retry_after_ms, and wait strictly longer than a transport retry."""
    requested = 0.0
    if retry_after_ms is not None:
        requested = max(0.0, float(retry_after_ms) / 1000.0)
    minimum = float(transport_retry_seconds) + 0.001
    return max(requested, minimum)


def login_retry_wait(now, last_login_at, interval=LOGIN_MIN_INTERVAL_S):
    """Seconds until another login may be sent. 0 means send."""
    if last_login_at is None:
        return 0.0
    remain = interval - (now - last_login_at)
    return remain if remain > 0 else 0.0


def observe_slot_wait(now, not_before, interval=OBSERVE_MIN_INTERVAL_S):
    """Seconds to wait so observe and heartbeat stay at one call per interval."""
    if not_before is None or now >= not_before:
        return 0.0
    return not_before - now


def classify_failure(code=None, exc=None):
    """Separate account_busy from rate_limited and from transport drops."""
    if code == "account_busy":
        return "account_busy"
    if code == "rate_limited":
        return "rate_limited"
    if code == "benchmark_selection_rejected":
        return "benchmark_selection_rejected"
    if isinstance(exc, (IncompleteRead, RemoteDisconnected)):
        return "transport"
    reason = getattr(exc, "reason", None)
    if isinstance(reason, (IncompleteRead, RemoteDisconnected)):
        return "transport"
    return "other"


def is_retryable_transport(exc):
    return classify_failure(exc=exc) == "transport"


def token_to_disconnect_on_busy(saved_token, current_token):
    """Token that may hold a prior lease.

    A newly issued token is never a substitute for the saved one, so the
    current connection is not returned.
    """
    if not saved_token:
        return None
    if current_token and saved_token == current_token:
        return None
    return saved_token


def require_int_guid(guid):
    """JSON integers only. Strings and bools are rejected before send."""
    if isinstance(guid, bool) or not isinstance(guid, int):
        raise CowCraftError(
            "guid must be a JSON integer",
            code="invalid_arguments",
        )
    return guid


def _atomic_write(path, text):
    path = os.fspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".writing"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def save_connection_token(path, token):
    """Persist the lease token and nothing else."""
    if not isinstance(token, str) or not token.strip() or any(
        ch.isspace() for ch in token
    ):
        raise CowCraftError("refusing to store an empty token", code="invalid_arguments")
    _atomic_write(path, token.strip() + "\n")


def load_connection_token(path):
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        return None
    line = text.splitlines()[0].strip() if text.strip() else ""
    return line or None


# Server said this token is already gone. Anything else (transport drop,
# HTTP error, rate limit) is uncertain: the lease may still be held.
_TOKEN_ALREADY_GONE = frozenset({"invalid_connection", "lease_expired"})


def clear_connection_token(path, token):
    """Remove the file only when it still holds this token."""
    if not token or load_connection_token(path) != token:
        return
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def save_model_name(path, model_name):
    """Persist the connect model_name unchanged, apart from a trailing newline."""
    if not isinstance(model_name, str) or model_name == "":
        raise CowCraftError("model_name is required", code="invalid_arguments")
    _atomic_write(path, model_name + "\n")


def load_model_name(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = handle.read()
    except FileNotFoundError:
        return None
    if data.endswith("\n"):
        data = data[:-1]
    return data or None


class CowCraft:
    def __init__(self, url=MCP_URL, token_path=None, model_path=None,
                 transport=None, clock=None, sleep=None):
        self.url = url
        self.token_path = token_path or DEFAULT_TOKEN_PATH
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.connection = None
        self.model_name = None
        self._transport = transport or self._default_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._msg_id = 0
        self._last_login_at = None
        self._next_observe_at = 0.0
        self._benchmark_rejected = False
        self._sent = []
        self._rpc_lock = threading.RLock()

    def _default_transport(self, url, payload, headers, timeout):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()[:200]
            raise CowCraftError(f"HTTP {exc.code}: {body}", code="http") from exc
        except urllib.error.URLError as exc:
            if is_retryable_transport(exc):
                raise
            raise CowCraftError(f"transport: {exc.reason}", code="transport") from exc

    def _parse_raw(self, raw):
        if raw.startswith("event:"):
            for line in raw.split("\n"):
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CowCraftError(f"invalid JSON: {exc}", code="invalid_json") from exc
        if isinstance(data, dict) and data.get("error"):
            raise CowCraftError(f"RPC error: {data['error']}", code="rpc")
        if not isinstance(data, dict):
            raise CowCraftError("RPC response was not an object", code="invalid_json")
        return data.get("result", {})

    def _exchange(self, payload):
        """POST one JSON-RPC message. Retry transport drops up to 3 failures.

        login is not resent: a dropped login still counts as the attempt,
        and the 60s gate is what allows another one.
        """
        headers = dict(_HEADERS)
        tool_name = None
        if payload.get("method") == "tools/call":
            tool_name = (payload.get("params") or {}).get("name")
        attempts = 1 if tool_name == "login" else MAX_TRANSPORT_ATTEMPTS
        last_exc = None
        for attempt in range(attempts):
            self._sent.append(json.loads(json.dumps(payload)))
            try:
                raw = self._transport(self.url, payload, headers, 30)
                return self._parse_raw(raw)
            except CowCraftError:
                raise
            except Exception as exc:
                if not is_retryable_transport(exc):
                    raise
                last_exc = exc
                if attempt + 1 >= attempts:
                    raise CowCraftError(
                        f"transport: {type(exc).__name__}",
                        code="transport",
                    ) from exc
                self._sleep(transport_backoff_seconds(attempt))
        raise CowCraftError(
            f"transport: {type(last_exc).__name__}",
            code="transport",
        )

    def _acquire_observe_slot(self):
        wait = observe_slot_wait(self._clock(), self._next_observe_at)
        if wait > 0:
            self._sleep(wait)
        self._next_observe_at = self._clock() + OBSERVE_MIN_INTERVAL_S

    def _rpc(self, method, params=None):
        params = params or {}
        tool_name = params.get("name") if method == "tools/call" else None
        with self._rpc_lock:
            if tool_name in ("observe", "heartbeat"):
                self._acquire_observe_slot()
            self._msg_id += 1
            payload = {
                "jsonrpc": "2.0",
                "id": self._msg_id,
                "method": method,
                "params": params,
            }
            return self._exchange(payload)

    def _tool_body(self, result):
        """Live CowCraft puts the payload in structuredContent. The stub uses content text."""
        if isinstance(result, dict):
            structured = result.get("structuredContent")
            if isinstance(structured, dict) and structured:
                return structured
        content = (result or {}).get("content") or [{}]
        text = "{}"
        if content and isinstance(content[0], dict):
            text = content[0].get("text", "{}")
        if isinstance(text, dict):
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}

    def _unwrap_tool(self, name, result):
        inner = self._tool_body(result)
        if isinstance(inner, dict) and isinstance(inner.get("error"), dict):
            err = inner["error"]
            code = err.get("code")
            retry = err.get("retry_after_ms", inner.get("retry_after_ms"))
            if classify_failure(code) == "rate_limited":
                self._sleep(rate_limit_wait_seconds(
                    retry, transport_backoff_seconds(0)
                ))
            raise CowCraftError(
                f"{name}: {code}: {err.get('message')}",
                code=code,
                retry_after_ms=retry,
            )
        return inner

    def _tool(self, name, arguments):
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        return self._unwrap_tool(name, result)

    def initialize(self):
        """MCP handshake. Safe to call more than once."""
        return self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "cowcraft-mcp", "version": "1.0"},
        })

    def list_tools(self):
        """Return the tools/list result object (one JSON object, unwrapped)."""
        result = self._rpc("tools/list", {})
        if not isinstance(result, dict) or "tools" not in result:
            raise CowCraftError(
                "tools/list: incomplete response",
                code="incomplete",
            )
        return result

    def connect(self, model_name=None):
        """Open a connection. Persist model_name and, if no lease is saved, the token.

        model_name is the caller's agent label, not an account. Pass the
        label for this user. Login takes that user's own username and
        password and does not substitute another account.

        An existing token file is left alone. The new token is not written
        over the lease a previous process still holds.
        """
        if model_name is None:
            model_name = load_model_name(self.model_path)
        if not isinstance(model_name, str) or model_name == "":
            raise CowCraftError("model_name is required", code="invalid_arguments")
        self.initialize()
        result = self._tool("connect", {"model_name": model_name})
        self.connection = result["connection"]
        self.model_name = model_name
        save_model_name(self.model_path, model_name)
        if not load_connection_token(self.token_path):
            save_connection_token(self.token_path, self.connection)
        return self.connection

    def _need_conn(self):
        if not self.connection:
            raise CowCraftError("Not connected. Call connect() first.")

    def register(self, username, password):
        self._need_conn()
        return self._tool("register", {
            "connection": self.connection,
            "username": username,
            "password": password,
        })

    def login(self, username, password):
        """Sign in as the given account. At most one send per 60s.

        Any username and password are sent as given. There is no built-in
        account.

        account_busy releases the saved lease token when it is not the
        token this process just issued, then waits. It does not start the
        60s login clock and it is not a rate limit.
        """
        self._need_conn()
        with self._rpc_lock:
            remain = login_retry_wait(self._clock(), self._last_login_at)
            if remain > 0:
                raise CowCraftError(
                    "login: login_suppressed: at most one login per 60 seconds",
                    code="login_suppressed",
                )
            try:
                result = self._tool("login", {
                    "connection": self.connection,
                    "username": username,
                    "password": password,
                })
            except CowCraftError as exc:
                if classify_failure(exc.code) == "account_busy":
                    self.release_saved_lease()
                    self._sleep(account_busy_wait_seconds())
                    raise
                self._last_login_at = self._clock()
                raise
            self._last_login_at = self._clock()
            if self.connection:
                save_connection_token(self.token_path, self.connection)
            return result

    def characters(self):
        self._need_conn()
        return self._tool("characters", {"connection": self.connection})

    def create_character(self, name, char_class=1, gender=0, race=1):
        """Create a level-1 Human. A non-Human race is not sent."""
        self._need_conn()
        if race != 1:
            raise CowCraftError(
                "create_character: humans_only: MCP agents can currently play Humans only",
                code="humans_only",
            )
        return self._tool("create_character", {
            "connection": self.connection,
            "character": {
                "name": name,
                "race": 1,
                "class": char_class,
                "gender": gender,
                "skin": 0,
                "face": 0,
                "hair_style": 0,
                "hair_color": 0,
                "facial_hair": 0,
            },
        })

    def delete_character(self, guid, confirm_name):
        self._need_conn()
        guid = require_int_guid(guid)
        return self._tool("delete_character", {
            "connection": self.connection,
            "guid": guid,
            "confirm_name": confirm_name,
        })

    def select_character(self, guid):
        """Enter the world. A string guid is rejected locally.

        benchmark_selection_rejected is raised once and not sent again.
        """
        self._need_conn()
        guid = require_int_guid(guid)
        if self._benchmark_rejected:
            raise CowCraftError(
                "select_character: benchmark_selection_rejected: not retrying",
                code="benchmark_selection_rejected",
            )
        try:
            return self._tool("select_character", {
                "connection": self.connection,
                "guid": guid,
            })
        except CowCraftError as exc:
            if classify_failure(exc.code) == "benchmark_selection_rejected":
                self._benchmark_rejected = True
            raise

    def observe(self):
        self._need_conn()
        return self._tool("observe", {"connection": self.connection})

    def heartbeat(self):
        self._need_conn()
        return self._tool("heartbeat", {"connection": self.connection})

    def action(self, action_name, params=None):
        self._need_conn()
        act = {"kind": action_name}
        if params:
            act.update(params)
        return self._tool("action", {
            "connection": self.connection,
            "action": act,
        })

    def list_actions(self):
        self._need_conn()
        return self._tool("list_actions", {"connection": self.connection})

    def stop(self):
        self._need_conn()
        return self._tool("stop", {"connection": self.connection})

    def disconnect(self, token=None):
        """Release one token.

        An explicit token, or this process's connection, is released as
        given. With neither, the token file is used. A fresh in-memory
        token is never swapped in for the saved lease.
        """
        if token is None:
            token = self.connection or load_connection_token(self.token_path)
        if not token:
            return {"disconnected": False}
        try:
            result = self._tool("disconnect", {"connection": token})
        except CowCraftError as exc:
            if exc.code not in _TOKEN_ALREADY_GONE:
                # IncompleteRead / RemoteDisconnected and any other uncertain
                # failure must leave the saved token in place.
                return {"disconnected": False}
            result = {"disconnected": False}
        else:
            if isinstance(result, dict) and result.get("disconnected") is False:
                return result
        if self.connection == token:
            self.connection = None
        clear_connection_token(self.token_path, token)
        return result

    def release_saved_lease(self):
        """Disconnect the persisted token when it is not the current one.

        Returns the token only after the file no longer holds it. A transport
        failure leaves the file alone and returns None.
        """
        target = token_to_disconnect_on_busy(
            load_connection_token(self.token_path),
            self.connection,
        )
        if not target:
            return None
        self.disconnect(token=target)
        if load_connection_token(self.token_path) == target:
            return None
        return target

    def self_state(self):
        state = self.observe()
        for unit in state.get("nearby_units", []):
            if unit.get("guid") == str(state.get("self_guid", "")):
                return unit
        return None

    def nearby_players(self):
        state = self.observe()
        return [u for u in state.get("nearby_units", [])
                if u.get("npc_flags", 0) == 0 and not u.get("dead", False)]

    def nearby_npcs(self):
        state = self.observe()
        return [u for u in state.get("nearby_units", [])
                if u.get("npc_flags", 0) != 0]


if __name__ == "__main__":
    # Do not connect. The production URL is the library default; launching
    # this file used to call it. The stub entry point is mcp_gateway.py.
    raise SystemExit(
        "cowcraft_mcp is a library. Launch mcp_gateway.py with BACKEND=stub."
    )
