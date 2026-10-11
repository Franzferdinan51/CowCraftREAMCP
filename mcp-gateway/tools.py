"""
MCP tool definitions for the Minta gateway.

17 tools, mirroring CowCraft's tools/list (server 0.3.18). Each entry: name, description,
inputSchema (JSON Schema). The gateway in gateway.py dispatches on these.
"""

TOOLS = [
    {
        "name": "connect",
        "description": (
            "Step 1 of connect, login, characters, select_character, observe. "
            "Create a game connection lease. No authentication required. "
            "model_name is the caller's stable agent label, reused for every "
            "later entry of that character. Returns a connection token, lease "
            "info, capabilities, and the connection instructions. "
            "Honor retry_after_ms; never busy-loop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_name": {"type": "string",
                               "description": "Identifying name of the AI model/agent"},
            },
            "required": ["model_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "register",
        "description": (
            "Create a new player account. No email, recovery or guest "
            "claiming. Credentials stay private. Five attempts per IP "
            "per five minutes. Requires an active connection lease."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "username": {"type": "string"},
                "password": {"type": "string",
                             "description": "10+ characters"},
            },
            "required": ["connection", "username", "password"],
            "additionalProperties": False,
        },
    },
    {
        "name": "login",
        "description": (
            "Step 2. Sign in as the caller. Any username and password are sent "
            "as given. One lease per account, and at most one login per 60 "
            "seconds. The server also allows five logins per account and twenty "
            "per IP each minute; honor retry_after_ms. account_busy is not "
            "rate_limited. A saved token for this account is resumed and left "
            "held; a later process releases only the backup. If login is still "
            "busy, wait and try once. Never busy-loop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
            "required": ["connection", "username", "password"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resume",
        "description": (
            "Recover this account after a dropped token or gateway restart. "
            "Send the same username, password, and exact model_name. The "
            "server returns a replacement connection and stops accepting the "
            "previous token. Optional character_guid enters only that "
            "character. Do not disconnect the old token first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "username": {"type": "string"},
                "password": {"type": "string"},
                "model_name": {"type": "string"},
                "character_guid": {"type": "integer"},
            },
            "required": ["username", "password", "model_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "characters",
        "description": "List the account's characters and available creation options.",
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_character",
        "description": (
            "Create a level-1 character. MCP agents can currently play "
            "Level-one Human (race 1) or Orc (race 2) only. "
            "Name must be 2-12 letters and unique."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "character": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "race": {"type": "integer"},
                        "class": {"type": "integer"},
                        "gender": {"type": "integer"},
                        "skin": {"type": "integer"},
                        "face": {"type": "integer"},
                        "hair_style": {"type": "integer"},
                        "hair_color": {"type": "integer"},
                        "facial_hair": {"type": "integer"},
                    },
                    "required": ["name", "race", "class"],
                    "additionalProperties": False,
                },
            },
            "required": ["connection", "character"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_character",
        "description": "Delete an offline character. Destructive; confirm_name must match.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "guid": {"type": "integer"},
                "confirm_name": {"type": "string"},
                "request_id": {"type": "string",
                               "description": "Idempotency key (optional)"},
            },
            "required": ["connection", "guid", "confirm_name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "select_character",
        "description": (
            "Enter the world with a character. First MCP entry requires "
            "level 1 and no Player Tools history; the character becomes "
            "permanently agent-bound (no Player Tools via any client)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "guid": {"type": "integer",
                         "description": "Character GUID as a JSON number"},
            },
            "required": ["connection", "guid"],
            "additionalProperties": False,
        },
    },
    {
        "name": "observe",
        "description": (
            "Get bounded, player-visible structured game state. Max 1/sec, "
            "budget shared with heartbeat. Lease clock on results/errors; "
            "native timeout 10s."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "sections": {"type": "array", "items": {"type": "string"}},
                "events_ack": {"type": "string"},
                "quest_id": {"type": "integer"},
                "spell_id": {"type": "integer"},
                "spell_offset": {"type": "integer"},
                "spell_limit": {"type": "integer"},
                "inventory_offset": {"type": "integer"},
                "inventory_limit": {"type": "integer"},
            },
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "observe_map",
        "description": (
            "Optional local terrain schematic. At most once every 5 seconds, "
            "and it also uses the 1/second navigation-read slot shared with "
            "navigation_probe, path_preview, and terrain_map. Not a screenshot. "
            "Ordinary observe stays independent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "observe_terrain_detail",
        "description": (
            "Optional forward terrain schematic. Visible ground over 120 "
            "degrees, every 2 yards out to 60 yards. Shares the five-second "
            "picture quota and the one-second navigation read. Not a "
            "screenshot or a verified route."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "observe_zone_map",
        "description": (
            "Zone map image with this character's position and heading. "
            "Only explored overlays are revealed. Shares the five-second "
            "picture quota and the one-second navigation read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "heartbeat",
        "description": (
            "Renew the lease without returning state. A client harness may "
            "call every 30-60s while thinking. Shares the 1/sec observation "
            "budget with observe."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "action",
        "description": (
            "Submit one typed game action. Max 20/sec per account. Serial "
            "calls per account. Unavailable to agents: Player Tools, instant "
            "levelling, map teleport, custom mounts, all chat, human-written "
            "mail. Lease clock on results/errors; native timeout 10s."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "request_id": {"type": "string"},
                "action": {
                    "type": "object",
                    "description": "Typed action; see list_actions for kinds",
                    "properties": {"kind": {"type": "string"}},
                    "required": ["kind"],
                    # per-kind validation happens in actions.validate_action
                },
            },
            "required": ["connection", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_actions",
        "description": "List authenticated, native-confirmed action names for this session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "connection": {"type": "string"},
                "family": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stop",
        "description": "Stop pending movement/combat for the selected character.",
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
    {
        "name": "disconnect",
        "description": "Close the control lease and release the account binding.",
        "inputSchema": {
            "type": "object",
            "properties": {"connection": {"type": "string"}},
            "required": ["connection"],
            "additionalProperties": False,
        },
    },
]


def tool_names():
    return [t["name"] for t in TOOLS]
