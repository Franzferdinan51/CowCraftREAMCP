# Minta MCP Gateway

A CowCraft-compatible MCP server for vanilla WoW (1.12.1). Implements the
reverse-engineered `cowcraft.world/mcp` protocol so AI agents can play on a
self-hosted server.

## Quick start

```sh
# stdlib only — no pip install needed
python3 gateway.py --host 127.0.0.1 --port 8787
```

Point an MCP client at `http://127.0.0.1:8787/mcp`. Health check at `/health`.

Set `MINTA_HMAC_SECRET` to a long random string in production — connection
tokens are HMAC-signed with it, and without a stable secret every restart
invalidates outstanding leases.

```sh
export MINTA_HMAC_SECRET="$(openssl rand -hex 32)"
export BACKEND=stub   # or: cowcraft, live
python3 mcp_gateway.py --port 8901
```
Prefer `mcp_gateway.py` as the entry point — it selects the backend from
`BACKEND` and then runs the server. (`gateway.py` works too but only knows
`stub`/`live`.)

## Files

| File | Purpose |
|---|---|
| `mcp_gateway.py` | Entry point: picks backend from `BACKEND`, launches server |
| `gateway.py` | HTTP server, JSON-RPC dispatch, 15 tool handlers |
| `tools.py` | MCP tool definitions (names, descriptions, input schemas) |
| `actions.py` | 161 game-action catalog + per-kind validation |
| `leases.py` | Token crypto (UUIDv4.HMAC-SHA256), lease lifecycle, rate limits |
| `backend.py` | `WoWBackend` abstract interface + `StubBackend` (in-memory fake world) |
| `cowcraft_backend.py` | `CowCraftBackend`: proxies to the real cowcraft.world |
| `cowcraft_mcp.py` | Vendored CowCraft MCP client (used by the proxy backend) |

## Backends

- **stub** (default): in-memory accounts/characters/sessions. Lets you
  exercise the whole protocol — register → login → create_character →
  select_character → observe/action — with no game server.
- **cowcraft**: proxies every call to the real `https://cowcraft.world/mcp`.
  Agents talk to your gateway; the gateway holds one upstream CowCraft
  session per agent account. You get auto-reconnect with backoff,
  stale-lease cleanup (fixes CowCraft's ghost-lease bug), per-account
  health stats, and optional request logging — plus a seam for custom
  tools. Credentials arrive via the `login` tool and live in memory only.
  ```sh
  BACKEND=cowcraft python3 mcp_gateway.py --port 8901
  # MINTA_COWCRAFT_URL  override upstream (default https://cowcraft.world/mcp)
  # MINTA_COWCRAFT_LOG  path for JSON-lines request log
  ```
- **live**: implement `live_backend.WoWBackendImpl` against your CMaNGOS
  instance (SOAP/RA console, native module, or headless-client farm), then
  `export BACKEND=live`.

## Tools added through server 0.3.18

The catalog is 17 tools. `resume` and `observe_map` arrived in 0.3.16.
`observe_terrain_detail` and `observe_zone_map` arrived in 0.3.18 and share
the five-second picture quota with `observe_map`. Action kinds added with
them are `terrain_detail` and `zone_map`.
Action kinds added with them: `navigation_probe`, `path_preview`,
`terrain_map`, `item_open`, `item_read_lore`, and `object_read_lore`.
Call them through the proxy, or on the direct client in `cowcraft_mcp.py`.
The repository README has copy-paste examples.

| Call | How |
|---|---|
| `resume` | Same username, password, and `model_name` as the dropped session. Optional `character_guid`. Do not disconnect the old token first. The gateway result omits `connection`; the direct client returns it and saves it. |
| `observe` | Optional `sections`, `events_ack`, `quest_id`, `spell_id`, `spell_offset`, `spell_limit`, `inventory_offset`, `inventory_limit`. |
| `observe_map` | Local terrain image. Once every 5 seconds. Also uses the navigation slot. |
| `navigation_probe`, `terrain_map` | Action kind only. Once per second, shared. They do not move the character. |
| `path_preview` | Action kind plus finite `x`, `y`, and `z`. Same one-per-second slot. |
| `face` | Exactly one of integer `guid` or finite `orientation`. |
| `cast` | `spell_id` from the latest observe, plus `target_guid`, when power can pay the listed cost. |
| `loot_open`, `loot_take`, `loot_money`, `loot_close` | Open by corpse guid, take integer slots, take money, close with the same guid. |
| `attack_stop` | Break off combat. |

`create_character` accepts race `1` (Human) or race `2` (Orc), and forwards
appearance integers. `select_character` and `delete_character` take an
integer guid. `delete_character` forwards `request_id` when you pass one.

## Protocol notes (from reverse engineering)

- Single-shot HTTP POST per JSON-RPC message; plain JSON responses.
- Auth is the `connection` token inside tool arguments, not HTTP headers.
- `connect` returns the server's connection instructions. Follow that order: connect, login, characters, select, observe, act, disconnect.
- Call `initialize` before `disconnect`, or the live lease is not released.
- Lease: about 600s pre-auth, about 120s in-world; `heartbeat` and `observe` renew it.
- One lease per account (`account_busy` otherwise). `account_busy` is not `rate_limited`. The client copies a saved token to `path.bak` before replacing it. That file has no password.
- A dead lease (`invalid_connection`, `lease_expired`, `not_authenticated`) is what signs in again.
- Rate limits, also enforced by the client: 1/sec observe+heartbeat, 20/sec actions, 1/sec shared by the three navigation reads, 1/5sec `observe_map`, 1/60sec login. The server text also allows five logins per account and twenty per IP each minute. Honor `retry_after_ms`. Calls per account are serial.
- Agents cannot use chat, teleports, Player Tools, or instant levelling (schema-enforced in `actions.py`).
- On `benchmark_selection_rejected`, do not retry that character.
