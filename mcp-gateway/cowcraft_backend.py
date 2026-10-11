#!/usr/bin/env python3
"""
cowcraft_backend.py — proxy the Minta gateway to the real cowcraft.world.

CowCraftBackend(WoWBackend) holds one upstream CowCraft MCP session per
account and forwards every gateway call to https://cowcraft.world/mcp via
the vendored cowcraft_mcp client.

Resilience (from reverse-engineering cowcraft.world):
- stale-lease cleanup: force-disconnect the old upstream token before
  every reconnect (CowCraft never reaps dead leases; without this the
  account stays `account_busy` for 1+ hour)
- transport retries: exponential backoff (1s/2s/4s) on IncompleteRead,
  connection drops, and `rate_limited`
- `account_busy`: kill stale token -> wait -> fresh connect -> retry once
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
from cowcraft_mcp import CowCraft, CowCraftError  # noqa: E402

UPSTREAM_URL = os.environ.get(
    "MINTA_COWCRAFT_URL", "https://cowcraft.world/mcp"
)
LOG_PATH = os.environ.get("MINTA_COWCRAFT_LOG")  # optional json-lines log

RECONNECT_COOLDOWN = 10.0   # seconds between reconnects per account
IDLE_REAP_AFTER = 600.0     # release upstream session after 10 min idle
BACKOFFS = (1.0, 2.0, 4.0)  # retry delays on transport failure


def _is_transport_error(exc):
    msg = str(exc).lower()
    return any(k in msg for k in (
        "incompleteread", "connection", "closed", "timeout",
        "temporarily", "rate_limited", "remote end",
    ))


class _Session:
    """One upstream CowCraft session for one account."""

    def __init__(self, username):
        self.username = username
        self.game = CowCraft(url=UPSTREAM_URL)
        self.lock = threading.RLock()
        self.password = None
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

    def __init__(self):
        self._sessions = {}
        self._guard = threading.Lock()
        self._log_lock = threading.Lock()

    # -- session management -------------------------------------------
    def _session(self, username):
        key = username.lower()
        with self._guard:
            s = self._sessions.get(key)
            if s is None:
                s = _Session(username)
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

    def _ensure_connected(self, s, password=None):
        """Return True if the upstream session is usable."""
        if password:
            s.password = password
        self._reap_if_idle(s)
        if s.connected:
            return True
        if time.time() - s.last_reconnect < RECONNECT_COOLDOWN:
            raise BackendError("rate_limited",
                               "reconnect cooldown; try again shortly")
        s.last_reconnect = time.time()
        # stale-lease cleanup first: CowCraft never reaps dead leases
        try:
            s.game.disconnect()
        except Exception:
            pass
        try:
            s.game.connect(f"minta-proxy-{s.username}")
            if not s.password:
                raise BackendError("not_authenticated",
                                   "login required before upstream use")
            s.game.login(s.username, s.password)
        except CowCraftError as exc:
            s.errors += 1
            s.last_error = str(exc)[:200]
            raise BackendError("upstream_error", str(exc)[:200])
        s.connected = True
        s.last_use = time.time()
        return True

    def _call(self, s, op, fn, *args, **kwargs):
        """Run fn with retries on transport failure / account_busy."""
        last_exc = None
        for attempt, delay in enumerate([0.0] + list(BACKOFFS)):
            if delay:
                time.sleep(delay)
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
                    if "account_busy" in msg.lower() and attempt == 0:
                        # kill the ghost lease and retry once
                        try:
                            s.game.disconnect()
                        except Exception:
                            pass
                        s.connected = False
                        last_exc = exc
                        continue
                    if _is_transport_error(exc):
                        s.connected = False  # force reconnect next try
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
                game = CowCraft(url=UPSTREAM_URL)
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

    def login(self, username, password):
        s = self._session(username)
        s.password = password  # held in memory only, for reconnects
        with s.lock:
            s.connected = False  # force a fresh upstream login
            self._ensure_connected(s)
            out = self._call(s, "login", lambda: {"username": username})
            return out

    def list_characters(self, username):
        s = self._session(username)
        return self._call(s, "list_characters", s.game.characters)

    def create_character(self, username, character):
        s = self._session(username)
        name = character.get("name", username)
        char_class = int(character.get("class", character.get("char_class", 1)))
        return self._call(
            s, "create_character",
            lambda: s.game.create_character(name, char_class=char_class))

    def delete_character(self, username, guid, confirm_name):
        s = self._session(username)
        return self._call(
            s, "delete_character",
            lambda: s.game.delete_character(int(guid)))

    def enter_world(self, username, character_guid):
        s = self._session(username)
        out = self._call(s, "enter_world",
                         lambda: s.game.select_character(int(character_guid)))
        s.in_world = True
        return out

    def leave_world(self, username):
        key = username.lower()
        with self._guard:
            s = self._sessions.pop(key, None)
        if s:
            with s.lock:
                try:
                    s.game.disconnect()
                except Exception:
                    pass
                s.connected = False
                s.in_world = False
                s.password = None
        return {"left": True}

    def observe(self, username, sections=None):
        s = self._session(username)
        return self._call(s, "observe", s.game.observe)

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
