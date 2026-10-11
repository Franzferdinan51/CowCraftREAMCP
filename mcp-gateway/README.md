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

## Protocol notes (from reverse engineering)

- Single-shot HTTP POST per JSON-RPC message; plain JSON responses.
- Auth is the `connection` token inside tool arguments, not HTTP headers.
- Lease: 600s pre-auth, 120s in-world; `heartbeat`/`observe` renew it.
- One lease per account (`account_busy` otherwise). Stale leases are
  reaped after 5 min idle (CowCraft never reaps — we fixed that).
- Rate limits: 1/sec observe+heartbeat, 20/sec actions, serial per account.
- Agents cannot use chat, teleports, Player Tools, or instant levelling
  (schema-enforced in `actions.py`).
