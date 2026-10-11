#!/usr/bin/env python3
"""
cowcraft_backend.py — proxy the Minta gateway to the real cowcraft.world.

CowCraftBackend(WoWBackend) holds one upstream CowCraft MCP session per
account and forwards every gateway call to https://cowcraft.world/mcp via
the vendored cowcraft_mcp client.

Resilience lives in cowcraft_mcp (one policy for this proxy and direct
clients):
- a saved lease is copied to its .bak file and left held; login does not
  disconnect that token. A later process releases the backup
- transport retries: exponential backoff on IncompleteRead and
  RemoteDisconnected (three failures stop)
- account_busy is not rate_limited: it does not start the login clock
- login is sent at most once per 60 seconds; rate_limited honors
  retry_after_ms and waits longer than a transport retry
- observe and heartbeat share one call per second
- reconnect cooldown: max 1 reconnect per 10s per account (no hammering)
- idle reaping: upstream sessions idle > 10 min are released
- per-account RLock: serial upstream calls even under gateway threading
- health(): per-account stats (uptime, errors, req/s, last error) —
  never includes passwords
- optional JSON-lines request log via MINTA_COWCRAFT_LOG

Credentials: passwords arrive via the gateway's login tool at runtime and
are held IN MEMORY ONLY for transparent reconnection. They are never
logged, never written to disk, and never appear in health() output.
"""

from __future__ import annotations

import json
import os
import threading
import time

from backend import WoWBackend, BackendError  # noqa: E402
from cowcraft_mcp import (  # noqa: E402
    CowCraft,
    CowCraftError,
    account_state_paths,
    park_saved_token,
    classify_failure,
    transport_backoff_seconds,
)

UPSTREAM_URL = os.environ.get(
    "MINTA_COWCRAFT_URL", "https://cowcraft.world/mcp"
)
LOG_PATH = os.environ.get("MINTA_COWCRAFT_LOG")  # optional json-lines log

RECONNECT_COOLDOWN = 10.0   # seconds between reconnects per account
IDLE_REAP_AFTER = 600.0     # release upstream session after 10 min idle


def proxy_transport_delay(attempt):
    """Same curve the client uses between IncompleteRead / RemoteDisconnected."""
    return transport_backoff_seconds(attempt)


def default_client_factory(username):
    """One upstream client per account, so accounts do not share a token file."""
    root = os.environ.get("MINTA_COWCRAFT_STATE", "/tmp/cowcraft-accounts")
    token_path, model_path = account_state_paths(root, username)
    return CowCraft(url=UPSTREAM_URL, token_path=token_path, model_path=model_path)


class _Session:
    """One upstream CowCraft session for one account."""

    def __init__(self, username, client_factory):
        self.username = username
        self.game = client_factory(username)
        self.lock = threading.RLock()
        self.password = None
        self.agent_label = None
        self.login_body = None
        self.connected = False
        self.in_world = False
        self.last_use = 0.0
        self.last_reconnect = 0.0
        self.created_at = time.time()
        self.requests = 0
        self.errors = 0
        self.last_error = None


class CowCraftBackend(WoWBackend):
    """WoWBackend that proxies to the real CowCraft MCP server."""

    def __init__(self, client_factory=None):
        self._client_factory = client_factory or default_client_factory
        self._sessions = {}
        self._guard = threading.Lock()
        self._log_lock = threading.Lock()

    # -- session management -------------------------------------------
    def _session(self, username):
        key = username.lower()
        with self._guard:
            s = self._sessions.get(key)
            if s is None:
                s = _Session(username, self._client_factory)
                self._sessions[key] = s
            return s

    def _log(self, username, op, ok, detail=""):
        if not LOG_PATH:
            return
        rec = {"ts": time.time(), "user": username, "op": op,
               "ok": ok, "detail": str(detail)[:200]}
        with self._log_lock:
            with open(LOG_PATH, "a") as f:
                f.write(json.dumps(rec) + "\n")

    def _reap_if_idle(self, s):
        if s.connected and time.time() - s.last_use > IDLE_REAP_AFTER:
            try:
                s.game.disconnect()
            except Exception:
                pass
            s.connected = False
            s.in_world = False

    def _ensure_connected(self, s, password=None, explicit_login=False):
        """Return True if the upstream session is usable.

        explicit_login is the caller asking to sign in. It is not a
        reconnect, so the 10s cooldown does not apply and does not get
        labeled rate_limited. Automatic reconnects still wait.
        """
        if password:
            s.password = password
        self._reap_if_idle(s)
        if s.connected:
            return True
        if not explicit_login and time.time() - s.last_reconnect < RECONNECT_COOLDOWN:
            raise BackendError("rate_limited",
                               "reconnect cooldown; try again shortly")
        if not explicit_login:
            s.last_reconnect = time.time()
        # Park a saved lease in the .bak file. Do not disconnect it: a later
        # process still has to release that token. Never touch another account.
        try:
            park_saved_token(s.game.token_path, account=s.username)
            s.game.connect(s.agent_label or s.username)
            if not s.password:
                raise BackendError("not_authenticated",
                                   "login required before upstream use")
            s.login_body = s.game.login(s.username, s.password)
        except BackendError:
            raise
        except CowCraftError as exc:
            s.errors += 1
            s.last_error = str(exc)[:200]
            kind = classify_failure(exc.code)
            if kind in ("account_busy", "rate_limited"):
                raise BackendError(kind, str(exc)[:200])
            raise BackendError("upstream_error", str(exc)[:200])
        s.connected = True
        s.last_use = time.time()
        return True

    def _call(self, s, op, fn, *args, **kwargs):
        """Run fn once. Transport backoff and login clocks live on the client.

        A transport failure that still escapes the client waits on the shared
        backoff curve and is tried once more. account_busy is not retried
        here and is not classified as rate_limited. A token parked in the
        backup is not disconnected.
        """
        last_exc = None
        for attempt in range(2):
            if attempt:
                time.sleep(proxy_transport_delay(attempt - 1))
            with s.lock:
                try:
                    self._ensure_connected(s)
                    out = fn(*args, **kwargs)
                    s.requests += 1
                    s.last_use = time.time()
                    self._log(s.username, op, True)
                    return out
                except BackendError:
                    raise
                except CowCraftError as exc:
                    msg = str(exc)
                    s.errors += 1
                    s.last_error = msg[:200]
                    self._log(s.username, op, False, msg)
                    kind = classify_failure(exc.code)
                    if kind in ("account_busy", "rate_limited", "benchmark_selection_rejected"):
                        raise BackendError(kind, msg[:200])
                    if kind == "transport" and attempt == 0:
                        # Retry the same call. A new login would burn the
                        # 60s window and could drop a lease the server still holds.
                        last_exc = exc
                        continue
                    raise BackendError("upstream_error", msg[:200])
        raise BackendError("upstream_error",
                           f"{op} failed after retries: {str(last_exc)[:150]}")

    # -- WoWBackend interface ------------------------------------------
    def register(self, username, password):
        s = self._session(username)
        with s.lock:
            try:
                self._ensure_connected(s, password)
            except BackendError as e:
                if e.code != "not_authenticated":
                    raise
                # register needs no prior login: use a bare client
                game = self._client_factory(username)
                try:
                    game.connect(f"minta-proxy-reg-{username}")
                    out = self._call_raw(game, "register",
                                         lambda: game.register(username, password))
                    return out
                except CowCraftError as exc:
                    raise BackendError("upstream_error", str(exc)[:200])
            return self._call(s, "register",
                              lambda: s.game.register(username, password))

    def _call_raw(self, game, op, fn):
        try:
            return fn()
        except CowCraftError as exc:
            raise BackendError("upstream_error", str(exc)[:200])

    def login(self, username, password, agent_label=None):
        """Sign in as the caller. agent_label is sent unchanged on connect."""
        s = self._session(username)
        s.password = password  # held in memory only, for reconnects
        if isinstance(agent_label, str) and agent_label != "":
            s.agent_label = agent_label
        elif not s.agent_label:
            s.agent_label = username
        with s.lock:
            # A session that already signed in stays usable. Forcing
            # connected off here made the next observe look rate_limited.
            if s.connected and s.login_body is not None:
                return s.login_body
            self._ensure_connected(s, explicit_login=True)
            return s.login_body

    def list_characters(self, username):
        s = self._session(username)
        return self._call(s, "list_characters", s.game.characters)

    def create_character(self, username, character):
        s = self._session(username)
        name = character.get("name", username)
        char_class = int(character.get("class", character.get("char_class", 1)))
        race = character.get("race", 1)
        try:
            race = int(race)
        except (TypeError, ValueError):
            race = 0
        return self._call(
            s, "create_character",
            lambda: s.game.create_character(
                name, char_class=char_class, race=race))

    def delete_character(self, username, guid, confirm_name):
        s = self._session(username)
        return self._call(
            s, "delete_character",
            lambda: s.game.delete_character(int(guid), confirm_name))

    def enter_world(self, username, character_guid):
        s = self._session(username)
        out = self._call(s, "enter_world",
                         lambda: s.game.select_character(int(character_guid)))
        s.in_world = True
        return out

    def leave_world(self, username):
        """Release the upstream lease. The result is the disconnect body.

        A transport failure leaves the saved token in place and is not
        reported as success.
        """
        key = username.lower()
        with self._guard:
            s = self._sessions.get(key)
        if s is None:
            return {"disconnected": False, "left": False}
        with s.lock:
            try:
                result = s.game.disconnect()
            except Exception:
                result = {"disconnected": False}
            if not isinstance(result, dict):
                result = {"disconnected": False}
            released = result.get("disconnected") is True
            if released:
                s.connected = False
                s.in_world = False
                s.password = None
                with self._guard:
                    if self._sessions.get(key) is s:
                        self._sessions.pop(key, None)
            out = dict(result)
            out["disconnected"] = released
            out["left"] = released
            return out

    def observe(self, username, sections=None, spell_limit=None, spell_offset=None):
        s = self._session(username)
        if sections is None and spell_limit is None and spell_offset is None:
            return self._call(s, "observe", s.game.observe)
        return self._call(
            s, "observe",
            lambda: s.game.observe(
                sections=sections,
                spell_limit=spell_limit,
                spell_offset=spell_offset,
            ))

    def do_action(self, username, action):
        s = self._session(username)
        if not isinstance(action, dict):
            raise BackendError("invalid_arguments", "action must be an object")
        name = action.get("kind") or action.get("name")
        params = action.get("params") or {
            k: v for k, v in action.items() if k not in ("kind", "name", "params")
        }
        if not name:
            raise BackendError("invalid_arguments", "action needs kind/name")
        return self._call(s, f"action:{name}",
                          lambda: s.game.action(name, params))

    def list_actions(self, username, family=None, kind=None):
        s = self._session(username)
        return self._call(s, "list_actions", s.game.list_actions)

    def stop(self, username):
        s = self._session(username)
        try:
            return self._call(s, "stop", s.game.stop)
        except BackendError:
            return {"stopped": True}

    # -- ops ------------------------------------------------------------
    def health(self):
        """Per-account upstream stats. Never includes passwords."""
        now = time.time()
        out = {}
        with self._guard:
            for key, s in self._sessions.items():
                out[key] = {
                    "connected": s.connected,
                    "in_world": s.in_world,
                    "uptime_s": round(now - s.created_at, 1),
                    "idle_s": round(now - s.last_use, 1) if s.last_use else None,
                    "requests": s.requests,
                    "errors": s.errors,
                    "last_error": s.last_error,
                }
        return {"upstream": UPSTREAM_URL, "sessions": out}
