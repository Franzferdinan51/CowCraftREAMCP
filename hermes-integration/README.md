# Hermes Integration

Hermes-agent-facing enhancements for the CowCraft WoW stack. The gateway is
the hands, SystemOne/LM Studio is the brain — this layer makes the whole
thing operable: headless bots, a Hermes skill, swarm coordination, and
session recording.

```
                    ┌──────────────┐
                    │ hermes-agent │  (Duckets' Mac shell)
                    └──────┬───────┘
                           │  natural language
              ┌────────────┴────────────┐
              │ hermes_skill/SKILL.md   │  "have Quacksmash attack the wolf"
              └────────────┬────────────┘
                           │  scripts/wow.py
              ┌────────────┴────────────┐
              │   Minta MCP gateway     │  13 tools, 155 actions
              │  (proxy → CowCraft)     │
              └────────────┬────────────┘
                           │  game world
                    ┌───────┴────────┐
                    │  cowcraft.world │
                    └────────────────┘
```

## Components

| File | What |
|---|---|
| `bot_mode.py` | Headless autonomous bot. JSON config, SIGTERM/SIGINT shutdown, rotating logs, `/health` endpoint, auto-reconnect. The unit of autonomous play. |
| `bot.json.example` | Example bot config (copy to `bot.json`, fill password or use `MINTA_PASSWORD`). |
| `hermes_skill/` | Hermes-agent skill package: `SKILL.md` + `scripts/wow.py` + `references/`. Lets Hermes drive characters from natural language. |
| `swarm_coordinator.py` | Multi-agent crew: role tasks (tank/scout/support), shared blackboard, leader election, formation offsets, parallel decisions (up to 4 via `auto_model_judge`). |
| `swarm.json.example` | Example 3-agent crew config. |
| `recorder.py` | Record every observe/decide/act tick to JSONL; replay, stats, and judge-verification modes. Training-data friendly. |

## Quick start

**1. Start the gateway** (proxy mode = real CowCraft):

```bash
cd ../mcp-gateway
BACKEND=cowcraft python3 mcp_gateway.py --port 8901
```

**2. Run one bot headless:**

```bash
cp bot.json.example mybot.json   # fill in username/password
MINTA_PASSWORD='...' python3 bot_mode.py --config mybot.json
curl -s http://127.0.0.1:8891/health | python3 -m json.tool
```

**3. Drive a character from Hermes** (after installing the skill):

> "have Quacksmash attack the nearest wolf"

Hermes loads `hermes_skill/SKILL.md` and runs `scripts/wow.py`.

**4. Run the swarm:**

```bash
cp swarm.json.example swarm.json  # fill in credentials
python3 swarm_coordinator.py --config swarm.json
```

**5. Record a session:**

```python
from recorder import TickLogger, RecordedEnv, record_decision
log = TickLogger("session.jsonl")
env = RecordedEnv(my_env, log)
# ... run loop, calling record_decision(log, action, conf, model) each tick
```

```bash
python3 recorder.py stats --in session.jsonl
python3 recorder.py replay --in session.jsonl --step
```

## Installing the skill in Hermes

Copy (or symlink) `hermes_skill/` into Hermes' skills directory:

```bash
cp -r hermes_skill ~/.hermes/skills/cowcraft-wow
# then: hermes skills list   # should show cowcraft-wow
```

The skill follows the Hermes SKILL.md format (frontmatter: name, description
≤60 chars, version, platforms, metadata tags/category; plus `scripts/` and
`references/`). See the [hermes-agent skill guide](https://github.com/nousresearch/hermes-agent/blob/HEAD/website/docs/developer-guide/creating-skills.md).

## Design rules (all components)

- **No new model loads.** Everything goes through `auto_model_judge`, which
  only uses already-loaded LM Studio models (prefers D1). RAM stays safe.
- **Credentials in env vars.** `MINTA_USERNAME` / `MINTA_PASSWORD` (or
  per-agent `MINTA_PASSWORD_<NAME>`). Never in chat, never committed.
- **Graceful degradation.** Model unreachable → `wait` instead of crashing.
  Gateway down → backoff reconnect. Leader dead → re-election.
- **Stdlib only.** No pip dependencies anywhere in this layer.

## Roadmap → full hermes-agent integration

- [x] Skill package in Hermes format (`hermes_skill/`)
- [x] Bot mode with health endpoint (supervisor-friendly)
- [x] Swarm coordinator with blackboard + leader election
- [ ] Hermes-native MCP client: register the Minta gateway in Hermes'
      `mcp_servers` config so tools appear directly (no `wow.py` shim)
- [ ] `hermes run "level my crew to 5"` — one-shot autonomous campaigns
- [ ] Cron: `hermes-agent` scheduled runs (nightly crew grinding while Duckets sleeps)
- [ ] Skill self-improvement: Hermes writes new WoW tactics as skills after observing the crew
