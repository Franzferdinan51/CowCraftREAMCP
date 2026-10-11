# Minta — Self-Hosted CowCraft Clone

A from-scratch rebuild of the cowcraft.world stack: vanilla WoW private
server + browser web client + MCP server for AI agents.

## What's here

```
/Volumes/Sabrent/Minta/
├── benilla/          # Web client (cloned from github.com/samwhosung/benilla)
├── mcp-gateway/      # MCP server — BUILT, tested, runs today
├── cmangos/          # Server build docs (not built yet)
└── MINTA_README.md   # this file
```

### 1. benilla — the web client ✅ cloned

A complete World of Warcraft 1.12.1 client written from scratch in
Rust + Bevy. MIT licensed. This is the same client CowCraft uses for
browser play.

- **Build:** `WOW_DATA=/path/to/WoW/Data cargo run --release -p benilla`
- Needs: stable Rust 1.98.1, a C compiler, an English 1.12.1 client
  (build 5875) for game data, and a 1.12.1 server with Warden off.
- See `benilla/README.md` and `benilla/docs/`.

### 2. mcp-gateway — the MCP server ✅ built & tested

Python (stdlib only, no dependencies). Replicates the reverse-engineered
`cowcraft.world/mcp` protocol:

- **15 tools**: connect, register, login, resume, characters, create_character,
  delete_character, select_character, observe, observe_map, heartbeat, action,
  list_actions, stop, disconnect
- **161 game-action capabilities** with per-kind schema validation
- Token leases (`UUIDv4.HMAC-SHA256`), 600s pre-auth / 120s in-world,
  heartbeat renewal, one-lease-per-account, stale-lease reaping
  (CowCraft's ghost-lease bug deliberately not copied)
- Rate limits: 1/sec observe+heartbeat, 20/sec actions, 1/sec navigation reads, 1/5sec observe_map, serial per account
- Agent restrictions schema-enforced: no chat, no teleports, no Player Tools

Run it:

```sh
cd mcp-gateway
export MINTA_HMAC_SECRET="$(openssl rand -hex 32)"  # production: keep stable
python3 gateway.py --host 127.0.0.1 --port 8787
# MCP endpoint: http://127.0.0.1:8787/mcp
```

**Backend modes** (`BACKEND` env var):
- `stub` (default): in-memory fake world. Full protocol works —
  register → login → create_character → select_character →
  observe/action — with no game server. For development and testing.
- `live`: implement `live_backend.WoWBackendImpl` per
  `backend.py`'s `WoWBackend` interface to wire a real CMaNGOS server.
  See `cmangos/BUILD.md` for the three wiring strategies
  (SOAP console → proxy → native module, easiest first).

The existing CowCraft Python client (`cowcraft_mcp.py`) works against this
gateway unchanged — just point it at the new URL.

### 3. cmangos — the game server ⏳ documented, not built

`cmangos/BUILD.md` has the exact build steps: which fork, CMake flags,
database setup, game-data extraction, config, and run instructions, plus
the three strategies for wiring the MCP gateway to it.

Compiling takes 30–90 min plus hours for map extraction — intentionally
left for a dedicated build session.

## Build order

1. **Now**: `mcp-gateway` runs standalone (`BACKEND=stub`). Develop
   agents against it today.
2. **Next**: build CMaNGOS per `cmangos/BUILD.md` (or use the
   [DadsMmoLab](https://github.com/dadsmmolab/dads-mmo-lab) Docker
   installer / [vanilla-andy-repack](https://github.com/sagrid-argus/vanilla-andy-repack)
   for instant gratification).
3. **Then**: implement `live_backend.py` (start with SOAP console
   commands — days, not weeks).
4. **Finally**: build benilla (`cargo run --release`), point it at your
   server, play in the browser next to your agents.

## What's working vs stubbed

| Piece | Status |
|---|---|
| MCP protocol (15 tools) | ✅ working, tested end-to-end |
| Lease/token/rate-limit logic | ✅ working |
| 161 action schemas + validation | ✅ working |
| Account/character management | ✅ working (stub backend) |
| observe/action game state | ⚠️ stubbed (fake positions; wire CMaNGOS for real) |
| Web client (benilla) | ✅ cloned, needs `cargo build` + 1.12.1 data |
| Game server (CMaNGOS) | ⏳ documented, not built |
| Gateway↔server bridge | ⏳ interface defined, needs `live_backend.py` |

## Reference

- Reverse engineering report: `~/workspace/skills/cowcraft/REVERSE_ENGINEERING.md`
- Original client: `~/workspace/skills/cowcraft/cowcraft_mcp.py`
- Protocol: MCP `2024-11-05`, Streamable HTTP, single-shot POST per message.
