---
name: cowcraft-wow
description: Control WoW characters via the Minta MCP gateway
version: 1.0.0
author: Duckets
license: MIT
platforms: [linux, macos]
metadata:
  tags: [wow, game, mcp, agent, cowcraft]
  category: games
  homepage: https://github.com/Franzferdinan51/CowCraftREAMCP
  requires_toolsets: [terminal]
---

# CowCraft WoW Control

Drive World of Warcraft (vanilla 1.12.1) characters through the Minta MCP
gateway — the self-hosted, CowCraft-protocol-compatible game bridge. Issue
natural-language game commands; this skill translates them into gateway
actions.

## When to use

The user asks you to do something inside World of Warcraft: move a character,
attack a target, loot, interact with NPCs, check status, or run an autonomous
bot. If it is even partially about controlling a WoW character, load this skill.

## Setup

The Minta gateway must be running (proxy mode = real CowCraft server):

```bash
cd /path/to/CowCraftREAMCP/mcp-gateway
BACKEND=cowcraft python3 mcp_gateway.py --port 8901
```

Credentials go in environment variables — never in chat:

```bash
export MINTA_USERNAME=DuckCrewTank
export MINTA_PASSWORD='...'
export MINTA_GATEWAY_URL=http://127.0.0.1:8901/mcp
```

## Quick commands

All examples use `scripts/wow.py` (in this skill's `scripts/` folder).

```bash
# Where am I / what's around me
python3 scripts/wow.py observe --character Quacksmash

# Move (cardinal step of ~6 world units)
python3 scripts/wow.py move --character Quacksmash --dir north

# Attack nearest hostile
python3 scripts/wow.py attack --character Quacksmash

# Loot nearest corpse
python3 scripts/wow.py loot --character Quacksmash

# Talk to / use nearest NPC or object
python3 scripts/wow.py interact --character Quacksmash

# Stop everything
python3 scripts/wow.py stop --character Quacksmash
```

## Natural language mapping

Translate the user's intent into the discrete action set:

| User says | Action |
|---|---|
| "go north / head east / walk toward X" | `move_north` / `move_south` / `move_east` / `move_west` (repeat steps as needed, re-observe between moves) |
| "attack the wolf / fight / engage" | `attack` (targets nearest hostile) |
| "loot it / pick it up" | `loot` |
| "talk to him / use that / pick up quest" | `interact` |
| "stop / hold / wait" | `stop` or `wait` |
| "what do you see / status" | `observe` (summarize: HP, position, nearby units/objects) |

After every 2–3 actions, run `observe` again and report back what changed.
Never claim movement succeeded without a fresh `observe` confirming the new
position.

## Autonomous bot mode

For long unattended runs, use `bot_mode.py` instead of step-by-step commands:

```bash
cd /path/to/CowCraftREAMCP/hermes-integration
python3 bot_mode.py --config mybot.json
```

The bot runs See > Decide > Act using the already-loaded LM Studio model
(never triggers new model loads), logs with rotation, exposes
`http://127.0.0.1:8891/health`, and reconnects automatically. Stop it with
SIGTERM/SIGINT or `kill <pid>`.

## Multi-agent swarm

To run a crew (tank/scout/support) with role-based coordination:

```bash
python3 swarm_coordinator.py --config swarm.json
```

See `swarm.json.example` for the config shape. Up to 4 agents decide in
parallel against the loaded model.

## Rules

- Never invent coordinates, HP values, or loot — always `observe` first.
- One gateway session per character; don't run two controllers on the same account.
- Respect rate limits: observe at most once per second.
- Credentials stay in env vars. Never print them, never commit them.
- The gateway fixes CowCraft's stale-lease bug (reaps idle leases); if you see
  `account_busy`, wait 10s and retry once before escalating.

## References

- `references/gateway-protocol.md` — the 13 MCP tools and 155 game actions
- `references/troubleshooting.md` — common failures and fixes
