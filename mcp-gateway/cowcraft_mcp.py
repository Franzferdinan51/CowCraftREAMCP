#!/usr/bin/env python3
"""
CowCraft MCP Client — play vanilla WoW via the Model Context Protocol.

CowCraft (https://cowcraft.world) is a private vanilla WoW server (CMaNGOS +
benilla web client) with an MCP server at /mcp for AI agent control.
No browser, screenshot, or GPU needed — pure structured API.

Usage:
    from cowcraft_mcp import CowCraft

    game = CowCraft()
    game.connect("Juno")                    # get connection lease
    game.login("Juno", "password")          # authenticate
    game.create_character("Name", char_class=1)  # Human Warrior
    game.select_character(guid)             # enter world
    state = game.observe()                  # get game state
    game.action("move", {"x": -8940, "y": -130, "z": 83})
    game.heartbeat()                         # keep lease alive
    game.disconnect()
"""

import json
import urllib.request
import urllib.error
import time

MCP_URL = "https://cowcraft.world/mcp"
PROTOCOL_VERSION = "2024-11-05"

# WoW vanilla class IDs
CLASSES = {
    1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue",
    5: "Priest", 7: "Shaman", 8: "Mage", 9: "Warlock", 11: "Druid",
}

# WoW vanilla race IDs (MCP currently supports Human=1 only)
RACES = {1: "Human"}


class CowCraftError(Exception):
    pass


class CowCraft:
    def __init__(self, url=MCP_URL):
        self.url = url
        self.connection = None
        self._msg_id = 0

    def _rpc(self, method, params=None):
        self._msg_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._msg_id,
            "method": method,
            "params": params or {},
        }
        # MCP handshake: initialize on first call
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
        except urllib.error.HTTPError as e:
            raise CowCraftError(f"HTTP {e.code}: {e.read().decode()[:200]}")

        # Handle SSE or plain JSON
        if raw.startswith("event:"):
            # Extract data: lines
            for line in raw.split("\n"):
                if line.startswith("data:"):
                    raw = line[5:].strip()
                    break

        data = json.loads(raw)
        if "error" in data:
            raise CowCraftError(f"RPC error: {data['error']}")
        return data.get("result", {})

    def _tool(self, name, arguments):
        result = self._rpc("tools/call", {
            "name": name,
            "arguments": arguments,
        })
        # Unwrap MCP content envelope
        content = result.get("content", [{}])[0]
        text = content.get("text", "{}")
        try:
            inner = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        # Check for game-level errors
        if isinstance(inner, dict) and "error" in inner:
            err = inner["error"]
            raise CowCraftError(f"{name}: {err.get('code')}: {err.get('message')}")
        return inner

    def _initialize(self):
        """MCP protocol handshake (called automatically by connect)."""
        return self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "juno", "version": "1.0"},
        })

    def connect(self, model_name="Juno"):
        """Create a direct game connection. Returns connection ID."""
        self._initialize()
        result = self._tool("connect", {"model_name": model_name})
        self.connection = result["connection"]
        return self.connection

    def _need_conn(self):
        if not self.connection:
            raise CowCraftError("Not connected. Call connect() first.")

    def register(self, username, password):
        """Create a new player account."""
        self._need_conn()
        return self._tool("register", {
            "connection": self.connection,
            "username": username,
            "password": password,
        })

    def login(self, username, password):
        """Sign in to an existing account."""
        self._need_conn()
        return self._tool("login", {
            "connection": self.connection,
            "username": username,
            "password": password,
        })

    def characters(self):
        """List account's characters."""
        self._need_conn()
        return self._tool("characters", {"connection": self.connection})

    def create_character(self, name, char_class=1, gender=0, race=1):
        """Create a level-1 Human character. class: 1=Warrior (default)."""
        self._need_conn()
        return self._tool("create_character", {
            "connection": self.connection,
            "character": {
                "name": name, "race": race, "class": char_class, "gender": gender,
                "skin": 0, "face": 0, "hair_style": 0,
                "hair_color": 0, "facial_hair": 0,
            },
        })

    def select_character(self, guid):
        """Enter the world with a character."""
        self._need_conn()
        return self._tool("select_character", {
            "connection": self.connection, "guid": guid,
        })

    def observe(self):
        """Get bounded player-visible game state (max 1/sec)."""
        self._need_conn()
        return self._tool("observe", {"connection": self.connection})

    def heartbeat(self):
        """Renew lease without returning state. Call every ~30s."""
        self._need_conn()
        return self._tool("heartbeat", {"connection": self.connection})

    def action(self, action_name, params=None):
        """Submit a typed game action (max 20/sec).
        action_name is the kind (e.g. 'move'), params holds kind-specific fields.
        Example: action('move', {'x': -8940, 'y': -130, 'z': 83})
        """
        self._need_conn()
        act = {"kind": action_name}
        if params:
            act.update(params)
        return self._tool("action", {
            "connection": self.connection,
            "action": act,
        })

    def list_actions(self):
        """List available action names."""
        self._need_conn()
        return self._tool("list_actions", {"connection": self.connection})

    def stop(self):
        """Stop pending movement/combat."""
        self._need_conn()
        return self._tool("stop", {"connection": self.connection})

    def disconnect(self):
        """Close the control lease."""
        if self.connection:
            try:
                self._tool("disconnect", {"connection": self.connection})
            except CowCraftError:
                pass
            self.connection = None

    # --- Convenience helpers ---

    def self_state(self):
        """Extract own character info from observe()."""
        state = self.observe()
        for unit in state.get("nearby_units", []):
            if unit.get("guid") == str(state.get("self_guid", "")):
                return unit
        # Fallback: find unit matching our character name
        return None

    def nearby_players(self):
        """List nearby player characters (not NPCs)."""
        state = self.observe()
        return [u for u in state.get("nearby_units", [])
                if u.get("npc_flags", 0) == 0 and not u.get("dead", False)]

    def nearby_npcs(self):
        """List nearby NPCs."""
        state = self.observe()
        return [u for u in state.get("nearby_units", [])
                if u.get("npc_flags", 0) != 0]


if __name__ == "__main__":
    import sys
    # Quick smoke test: connect only
    game = CowCraft()
    conn = game.connect("Juno-test")
    print(f"Connected: {conn[:20]}...")
    game.disconnect()
    print("OK")
