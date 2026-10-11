"""
Backend interface for the Minta MCP gateway.

This module defines the abstract WoW-server backend. The gateway calls into
it; a real implementation wires it to CMaNGOS (via a native bridge, SOAP,
or a headless-client farm). The bundled StubBackend implements the full
interface with in-memory state so the gateway, protocol, leases, and
schemas can be developed and tested without a game server.

To go live:
    1. Implement WoWBackend against your CMaNGOS instance.
    2. Point gateway.py at it (BACKEND env var or import).
    3. Flip GATEWAY_MODE from "stub" to "live".
"""

import time


class BackendError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class WoWBackend:
    """Abstract backend. All methods may raise BackendError(code, message)."""

    # -- accounts ---------------------------------------------------
    def register(self, username, password):
        raise NotImplementedError

    def login(self, username, password):
        """Returns account dict. Must enforce agent-bind rules."""
        raise NotImplementedError

    def list_characters(self, username):
        raise NotImplementedError

    def create_character(self, username, character):
        raise NotImplementedError

    def delete_character(self, username, guid, confirm_name):
        raise NotImplementedError

    # -- world ------------------------------------------------------
    def enter_world(self, username, character_guid):
        """Bind session; returns initial snapshot summary."""
        raise NotImplementedError

    def leave_world(self, username):
        raise NotImplementedError

    def observe(self, username, sections=None):
        """Return bounded world-state snapshot dict."""
        raise NotImplementedError

    def do_action(self, username, action):
        """Execute one validated game action. Returns result dict."""
        raise NotImplementedError

    def list_actions(self, username, family=None, kind=None):
        raise NotImplementedError

    def stop(self, username):
        raise NotImplementedError


class StubBackend(WoWBackend):
    """
    In-memory stub. Implements the full interface with fake state.
    Enough to exercise the whole MCP protocol end-to-end.
    """

    def __init__(self):
        self.accounts = {}   # username.lower() -> {"password":..., "characters":[...]}
        self.sessions = {}   # username.lower() -> {"guid":..., "x":..,"y":..,"z":..,"hp":..}
        self._next_guid = 5000

    # -- helpers ----------------------------------------------------
    def _acct(self, username):
        acct = self.accounts.get(username.lower())
        if acct is None:
            raise BackendError("invalid_credentials", "unknown account")
        return acct

    def _check_pw(self, username, password):
        acct = self._acct(username)
        if acct["password"] != password:
            raise BackendError("invalid_credentials", "bad password")
        return acct

    # -- accounts ---------------------------------------------------
    def register(self, username, password):
        if not username or len(username) < 2:
            raise BackendError("invalid_arguments", "username too short")
        if not password or len(password) < 10:
            raise BackendError("invalid_arguments",
                               "password must be 10+ characters")
        key = username.lower()
        if key in self.accounts:
            raise BackendError("username_taken", "username already exists")
        self.accounts[key] = {"username": username, "password": password,
                              "characters": [], "agent_bound": False}
        return {"username": username}

    def login(self, username, password):
        acct = self._check_pw(username, password)
        return {"username": acct["username"],
                "characters": len(acct["characters"])}

    def list_characters(self, username):
        acct = self._acct(username)
        return {"characters": acct["characters"],
                "creation_options": {
                    "races": [1],
                    "classes": [1, 2, 3, 4, 5, 7, 8, 9, 11],
                    "note": "MCP agents can currently play Humans only (race=1)",
                }}

    def create_character(self, username, character):
        acct = self._acct(username)
        name = character.get("name", "")
        if not (2 <= len(name) <= 12) or not name.isalpha():
            raise BackendError("invalid_arguments",
                               "name must be 2-12 letters")
        if character.get("race") != 1:
            raise BackendError("invalid_arguments",
                               "MCP agents can currently play Humans only")
        for c in acct["characters"]:
            if c["name"].lower() == name.lower():
                raise BackendError("name_taken", "character name taken")
        guid = self._next_guid
        self._next_guid += 1
        char = {"guid": guid, "name": name,
                "race": 1, "class": character.get("class", 1),
                "level": 1, "agent_bound": True}
        acct["characters"].append(char)
        return {"character": char}

    def delete_character(self, username, guid, confirm_name):
        acct = self._acct(username)
        for i, c in enumerate(acct["characters"]):
            if c["guid"] == guid:
                if c["name"].lower() != (confirm_name or "").lower():
                    raise BackendError("invalid_arguments",
                                       "confirm_name does not match")
                del acct["characters"][i]
                return {"deleted": guid}
        raise BackendError("invalid_arguments", "character not found")

    # -- world ------------------------------------------------------
    def enter_world(self, username, character_guid):
        acct = self._acct(username)
        char = next((c for c in acct["characters"]
                     if c["guid"] == character_guid), None)
        if char is None:
            raise BackendError("invalid_arguments", "character not found")
        # Northshire Abbey spawn, like CowCraft
        self.sessions[username.lower()] = {
            "guid": character_guid, "x": -8949.95, "y": -132.49,
            "z": 83.53, "hp": 60, "max_hp": 60, "level": char["level"],
            "name": char["name"],
        }
        return {"character": char}

    def leave_world(self, username):
        self.sessions.pop(username.lower(), None)
        return {}

    def observe(self, username, sections=None):
        sess = self.sessions.get(username.lower())
        if sess is None:
            raise BackendError("not_in_world", "no character in world")
        return {
            "self": {
                "guid": str(sess["guid"]), "name": sess["name"],
                "level": sess["level"],
                "health": sess["hp"], "max_health": sess["max_hp"],
                "x": sess["x"], "y": sess["y"], "z": sess["z"],
                "faction": 1, "hostile": False, "dead": sess["hp"] <= 0,
            },
            "nearby_units": [],
            "nearby_objects": [],
            "quests": [],
            "auras": [],
            "combat_ui": {"in_combat": False},
            "stub": True,
            "stub_note": "wire a real CMaNGOS backend for live world state",
        }

    def do_action(self, username, action):
        sess = self.sessions.get(username.lower())
        if sess is None:
            raise BackendError("not_in_world", "no character in world")
        kind = action["kind"]
        # Simulate movement so the protocol is exercisable
        if kind == "move":
            sess["x"] = float(action["x"])
            sess["y"] = float(action["y"])
            sess["z"] = float(action.get("z", sess["z"]))
            return {"moved": True, "x": sess["x"], "y": sess["y"],
                    "z": sess["z"]}
        if kind == "stop":
            return {"stopped": True}
        if kind == "release_spirit":
            sess["hp"] = 1
            return {"released": True, "stub": True}
        # Everything else: acknowledge as stubbed
        return {"ok": True, "kind": kind, "stub": True,
                "stub_note": "wire a real CMaNGOS backend to execute"}

    def list_actions(self, username, family=None, kind=None):
        from actions import action_names
        names = action_names()
        if kind:
            names = [n for n in names if n == kind]
        # family filtering is a future refinement; accept and ignore
        return {"actions": names, "native_confirmed": False,
                "stub": True}

    def stop(self, username):
        return {"stopped": True}
