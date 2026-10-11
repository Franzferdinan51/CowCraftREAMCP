# Gateway protocol — quick reference

The Minta gateway speaks the CowCraft MCP protocol: Streamable HTTP
(one JSON-RPC message per POST, plain JSON response) at `/mcp`.

## MCP methods

- `initialize` → server info (`minta` v0.1.0, protocol `2024-11-05`)
- `tools/list` → the 13 tools with JSON input schemas
- `tools/call` → invoke a tool by name

## The 13 tools

| Tool | Args | Purpose |
|---|---|---|
| `connect` | `model_name` | Open a session. Returns `connection` token (UUIDv4.HMAC-SHA256) + 155 capabilities |
| `register` | `connection`, `username`, `password` | Create account (password 10+ chars) |
| `login` | `connection`, `username`, `password` | Authenticate the session |
| `characters` | `connection` | List account characters |
| `create_character` | `connection`, `name`, `char_class` | Create Human (race=1); name 2–12 letters |
| `delete_character` | `connection`, `guid` | Delete an offline character (destructive) |
| `select_character` | `connection`, `guid` | Enter the world |
| `observe` | `connection` | Full game state. Max 1/sec. Renews lease |
| `heartbeat` | `connection` | Renew lease. No state returned |
| `action` | `connection`, `name`, `params` | Submit a game action. Max 20/sec |
| `list_actions` | `connection` | The 155 capability schemas |
| `stop` | `connection` | Stop movement/combat |
| `disconnect` | `connection` | Close the session |

## Leases

- Pre-auth: ~600s. In-world: ~120s.
- `observe` and `heartbeat` both renew (shared 1/sec budget).
- One lease per account — a second login gets `account_busy`.
- **Stale leases are reaped after 5 min idle** (CowCraft's ghost-lease bug is fixed here).

## Rate limits

- `observe`: 1/sec per session
- `action`: 20/sec per session
- Calls per account are serial; honor `retry_after_ms`.

## Game errors (inside 200-OK tool results)

- `account_busy` — another session holds the account; wait 10s, retry once
- `rate_limited` — back off for `retry_after_ms`
- `invalid_arguments` — schema violation; check `list_actions` for the action's params
- `not_authenticated` — `login` first

## Agent restrictions (schema-enforced)

No chat, no teleports, no instant levelling, no Player Tools, no custom
mounts, no human-written mail. First MCP entry permanently marks the
character agent-bound.
