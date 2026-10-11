# Gateway protocol — quick reference

The Minta gateway speaks the CowCraft MCP protocol: Streamable HTTP
(one JSON-RPC message per POST, plain JSON response) at `/mcp`.

## MCP methods

- `initialize` → server info (`minta` v0.1.0, protocol `2024-11-05`)
- `tools/list` → the 15 tools with JSON input schemas
- `tools/call` → invoke a tool by name

## The 15 tools

| Tool | Args | Purpose |
|---|---|---|
| `connect` | `model_name` | Open a session. Returns `connection` token (UUIDv4.HMAC-SHA256) + 161 capabilities |
| `register` | `connection`, `username`, `password` | Create account (password 10+ chars). Five tries per IP per five minutes |
| `login` | `connection`, `username`, `password` | Authenticate. Five per account and twenty per IP each minute. Client also waits 60s |
| `resume` | `username`, `password`, `model_name` | Same model name replaces the connection. Optional `character_guid`. Do not disconnect the old token first |
| `characters` | `connection` | List account characters |
| `create_character` | `connection`, `character` | Level-one Human (race 1) or Orc (race 2). Name 2–12 letters |
| `delete_character` | `connection`, `guid`, `confirm_name` | Delete an offline character. Reuse `request_id` if the result is uncertain |
| `select_character` | `connection`, `guid` | Enter the world. Guid is a JSON number |
| `observe` | `connection` | Structured state. Max 1/sec. Optional `events_ack`, spell and inventory pages. No `items` flag |
| `observe_map` | `connection` | Local terrain image. At most once every 5 seconds. Not a screenshot |
| `heartbeat` | `connection` | Renew lease. No state returned. Shares the observe slot |
| `action` | `connection`, `action` | One typed action. Max 20/sec. Optional `request_id` |
| `list_actions` | `connection` | Authenticated action names. Optional `family` or `kind` |
| `stop` | `connection` | Stop pending movement or combat when the server supports it |
| `disconnect` | `connection` | Close the session. Does not delete the character |

## Leases

- Pre-auth: ~600s. In-world: ~120s.
- `observe` and `heartbeat` both renew (shared 1/sec budget).
- One lease per account — a second login gets `account_busy`.
- **Stale leases are reaped after 5 min idle** (CowCraft's ghost-lease bug is fixed here).

## Rate limits

- `observe` and `heartbeat`: 1/sec per session
- `action`: 20/sec per session
- `navigation_probe`, `path_preview`, and `terrain_map`: 1/sec shared, and they do not move
- `observe_map`: 1/5sec, and it also uses that navigation slot
- `login`: five per account and twenty per IP each minute; this client also waits 60 seconds
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
