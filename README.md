# CowCraftREAMCP

Self-hosted vanilla WoW (1.12.1) MCP stack for AI agents — reverse-engineered
from [cowcraft.world](https://cowcraft.world), rebuilt as open components.

**Brain + hands:** [SystemOne](https://github.com/Franzferdinan51/SystemOne)
routes models and makes typed decisions; the Minta MCP gateway controls the
game. Agents think via SystemOne, act through the gateway.

```
Agent tick:
  SEE     gateway observe  -> world state as text
  DECIDE  SystemOne judge  -> one typed action choice (+ gates)
  ACT     gateway action() -> CowCraft (via proxy) or your own CMaNGOS
```

## Layout

| Path | What |
|---|---|
| `mcp-gateway/` | CowCraft-protocol MCP server (13 tools, 155 actions). Backends: `stub` (fake world), `cowcraft` (proxy to real cowcraft.world), `live` (your CMaNGOS). Stdlib Python only. |
| `systemone-integration/` | SystemOne `Env` + judges + single-agent runner + multi-agent swarm. Per-agent model routing, shared blackboard. |
| `cmangos/` | Build docs for the WoW server backend. |
| `docs/` | `REVERSE_ENGINEERING.md` (full protocol spec), `MINTA_README.md` (build order), benilla notes. |

Not in this repo (too large / not ours):
- **benilla** web client — clone separately: `git clone https://github.com/samwhosung/benilla`
- **SystemOne** — clone separately: `git clone https://github.com/Franzferdinan51/SystemOne`
- **WoW 1.12.1 game data** — user-supplied, never committed

## Quick start

```sh
# 1. gateway (proxy mode = real CowCraft world)
cd mcp-gateway
BACKEND=cowcraft python3 mcp_gateway.py --port 8901

# 2. one agent (SystemOne shim optional; falls back to stub judge)
cd ../systemone-integration
MINTA_USERNAME=Juno MINTA_PASSWORD='...' \
  python3 minta_agent.py --name Junoduck --role scout --ticks 40

# 3. the crew
cp swarm_config.example.json swarm_config.json  # edit accounts/models
MINTA_PASSWORD='...' python3 swarm.py --config swarm_config.json
```

## Docs

- `docs/REVERSE_ENGINEERING.md` — the full cowcraft.world/mcp protocol spec:
  13 tools, 155 action schemas, lease lifecycle, rate limits, architecture
  inference, and the replication guide.
- `mcp-gateway/README.md` — gateway backends, protocol notes, env vars.
- `systemone-integration/README.md` — agent loop, judges, swarm config,
  model routing, extending the action set.
- `cmangos/BUILD.md` — building the WoW server backend.

## Status

- [x] MCP protocol replicated (existing CowCraft clients work unmodified)
- [x] Proxy backend to real cowcraft.world (auto-reconnect, stale-lease fix)
- [x] SystemOne integration (Env, judges, swarm)
- [ ] Live CMaNGOS backend (`live_backend.WoWBackendImpl` — interface defined)
- [ ] benilla headless agent client (protocol crates identified as the seed)

## License

Our code: MIT. benilla/SystemOne/CMaNGOS keep their own licenses.
Not affiliated with Blizzard.
