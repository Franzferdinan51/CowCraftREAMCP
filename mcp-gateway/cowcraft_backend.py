#!/usr/bin/env python3
"""
cowcraft_backend.py — proxy the Minta gateway to the real cowcraft.world.

CowCraftBackend(WoWBackend) holds one upstream CowCraft MCP session per
account and forwards every gateway call to https://cowcraft.world/mcp via
the vendored cowcraft_mcp client.

Resilience lives in cowcraft_mcp (one policy for this proxy and direct
clients):
- a saved lease for this account is resumed in place. Login does not
  disconnect it, does not open a second connection, and copies it to
  the .bak file first. A later process releases that backup. A dead
  lease is the only case that signs in again
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
    backup_connection_token,
    classify_failure,
    load_connection_owner,
    load_connection_token,
    transport_backoff_seconds,
)

UPSTREAM_URL = os.environ.get(
    "MINTA_COWCRAFT_URL", "https://cowcraft.world/mcp"
)
LOG_PATH = os.environ.get("MINTA_COWCRAFT_LOG")  # optional json-lines log

RECONNECT_COOLDOWN = 10.0   # seconds between reconnects per account
IDLE_REAP_AFTER = 600.0     # release upstream session after 10 min idle
# The server has already dropped these. A fresh sign-in will not
# collide with them, and they must not be disconnected again.
_DEAD_LEASE = frozenset({"invalid_connection", "lease_expired", "not_authenticated"})


def proxy_transport_delay(attempt):
    """Same curve the client uses between IncompleteRead / RemoteDisconnected."""
    return transport_backoff_seconds(attempt)


def default_client_factory(username):
    """One upstream client per account, so accounts do not share a token file."""
    root = os.environ.get("MINTA_COWCRAFT_STATE", "/tmp/cowcraft-accounts")
    token_path, model_path = account_state_paths(root, username)
    return CowCraft(url=UPSTREAM_URL, token_path=token_path, model_path=model_path)


def preserved_upstream_error(exc):
    """Keep the upstream code. account_busy is still not rate_limited."""
    kind = classify_failure(getattr(exc, "code", None), exc)
    if kind in ("account_busy", "rate_limited", "benchmark_selection_rejected"):
        code = kind
    else:
        code = getattr(exc, "code", None) or "upstream_error"
    return BackendError(code, str(exc)[:200])


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
        # A token already on disk is this account's live lease. Resume it.
        # Parking it and opening another connection makes the server answer
        # account_busy, or drops the lease a later process still has to release.
        if self._resume_saved_lease(s):
            return True
        try:
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
            if kind == "account_busy":
                # connect() stored this attempt's token. It is not the lease
                # the server is holding, and resuming it would skip the retry.
                self._drop_rejected_connection(s)
            if kind in ("account_busy", "rate_limited"):
                raise BackendError(kind, str(exc)[:200])
            raise BackendError("upstream_error", str(exc)[:200])
        s.connected = True
        s.last_use = time.time()
        return True

    def _resume_saved_lease(self, s):
        """Adopt this account's saved token. Do not connect, login, or disconnect.

        The token is copied to the .bak file and left in the active file.
        characters() is the proof the lease still answers. A dead lease
        returns False so the caller can sign in once. Transport loss raises
        and leaves both copies in place. Another account's file is not read
        past the owner check and is not modified.
        """
        path = s.game.token_path
        token = load_connection_token(path)
        if not token:
            return False
        owner = load_connection_owner(path)
        if owner and s.username and owner != s.username:
            return False
        backup_connection_token(path)
        s.game.connection = token
        try:
            s.game.initialize()
            s.game.characters()
        except CowCraftError as exc:
            if exc.code in _DEAD_LEASE:
                s.game.connection = None
                return False
            kind = classify_failure(exc.code)
            if kind in ("account_busy", "rate_limited"):
                raise BackendError(kind, str(exc)[:200])
            raise BackendError("upstream_error", str(exc)[:200])
        s.login_body = {
            "username": s.username,
            "resumed": True,
            "state": "resumed",
        }
        s.connected = True
        s.last_use = time.time()
        return True

    def _drop_rejected_connection(self, s):
        """Remove a connection token that login refused.

        A token already copied to .bak or .held is a lease a later process
        still has to release, so the active file is left alone in that case.
        """
        path = s.game.token_path
        current = s.game.connection
        if not current or load_connection_token(path) != current:
            return
        for suffix in (".bak", ".held"):
            if load_connection_token(os.fspath(path) + suffix) == current:
                return
        for suffix in ("", ".owner"):
            try:
                os.remove(os.fspath(path) + suffix)
            except FileNotFoundError:
                pass

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
        """Open a connection and register. Do not login first.

        A missing account used to be signed in before register, so the
        server answered invalid_credentials and the register tool was never
        sent. username_taken is returned as username_taken. The connection
        used only for register is released and is not kept as the login lease.
        """
        s = self._session(username)
        with s.lock:
            s.password = password
            if not s.agent_label:
                s.agent_label = username
            try:
                s.game.connect(s.agent_label)
                out = s.game.register(username, password)
            except CowCraftError as exc:
                self._release_register_connection(s)
                raise preserved_upstream_error(exc)
            self._release_register_connection(s)
            return out

    def _release_register_connection(self, s):
        if s.game.connection:
            try:
                s.game.disconnect()
            except Exception:
                s.game.connection = None
        s.connected = False

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
        def appearance(key):
            try:
                return int(character.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        return self._call(
            s, "create_character",
            lambda: s.game.create_character(
                name, char_class=char_class, race=race,
                gender=appearance("gender"),
                skin=appearance("skin"),
                face=appearance("face"),
                hair_style=appearance("hair_style"),
                hair_color=appearance("hair_color"),
                facial_hair=appearance("facial_hair")))

    def delete_character(self, username, guid, confirm_name, request_id=None):
        s = self._session(username)
        return self._call(
            s, "delete_character",
            lambda: s.game.delete_character(
                int(guid), confirm_name, request_id=request_id))

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

    def observe(self, username, sections=None, spell_limit=None, spell_offset=None,
                events_ack=None, quest_id=None, spell_id=None,
                inventory_offset=None, inventory_limit=None):
        s = self._session(username)
        if (sections is None and spell_limit is None and spell_offset is None
                and events_ack is None and quest_id is None and spell_id is None
                and inventory_offset is None and inventory_limit is None):
            return self._call(s, "observe", s.game.observe)
        return self._call(
            s, "observe",
            lambda: s.game.observe(
                sections=sections,
                spell_limit=spell_limit,
                spell_offset=spell_offset,
                events_ack=events_ack,
                quest_id=quest_id,
                spell_id=spell_id,
                inventory_offset=inventory_offset,
                inventory_limit=inventory_limit,
            ))

    def observe_map(self, username):
        s = self._session(username)
        return self._call(s, "observe_map", s.game.observe_map)

    def observe_terrain_detail(self, username):
        s = self._session(username)
        return self._call(s, "observe_terrain_detail", s.game.observe_terrain_detail)

    def observe_zone_map(self, username):
        s = self._session(username)
        return self._call(s, "observe_zone_map", s.game.observe_zone_map)

    def resume_session(self, username, password, model_name, character_guid=None):
        s = self._session(username)
        s.password = password
        s.agent_label = model_name

        def call():
            body = s.game.resume(
                username, password, model_name, character_guid=character_guid)
            s.connected = True
            s.login_body = body if isinstance(body, dict) else {"resumed": True}
            if character_guid is not None and isinstance(body, dict) and body.get("resumed"):
                s.in_world = True
            if isinstance(body, dict):
                return {key: value for key, value in body.items()
                        if key != "connection"}
            return {"resumed": True}

        return self._call(s, "resume", call)

    def do_action(self, username, action, request_id=None):
        s = self._session(username)
        if not isinstance(action, dict):
            raise BackendError("invalid_arguments", "action must be an object")
        name = action.get("kind") or action.get("name")
        params = action.get("params") or {
            k: v for k, v in action.items() if k not in ("kind", "name", "params")
        }
        if not name:
            raise BackendError("invalid_arguments", "action needs kind/name")
        return self._call(
            s, "action:%s" % name,
            lambda: s.game.action(name, params, request_id=request_id))

    def list_actions(self, username, family=None, kind=None):
        s = self._session(username)
        return self._call(
            s, "list_actions",
            lambda: s.game.list_actions(family=family, kind=kind))

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
