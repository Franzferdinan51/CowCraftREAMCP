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
              │   Minta MCP gateway     │  15 tools, 161 actions
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
| `recorder.py` | Append private training rows outside the repo, then replay, stats, or verify. Off unless the bot config sets `record` true or `COWCRAFT_RECORD=1`. |

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

**5. What a tick does**

`bot_mode.py` registers the username when this client has not seen it, sends one login, and creates a level-1 Human (race 1) or Orc (race 2) only when no character can be entered. Set `race` and the appearance integers in the bot config. Other races are refused before create is sent. The skill helper uses the same path: `scripts/wow.py account` and `scripts/wow.py character`.

`bot_mode.py` observes, then asks already-loaded `d1-3b` and `d1-omni-600m` what the character could do and what it could use. That text is prefixed `Decision advice, not required.` The main model is the first loaded id that is neither of those two. Its reply is the action. No main model means `wait`, and the advice is not executed. Nothing in this layer loads a model.

The action set is `move_north`, `move_south`, `move_east`, `move_west`, `attack`, `cast`, `stop`, `interact`, `loot`, `stats`, and `wait`. `attack` and `cast` target the nearest hostile and cast one spell the server listed when power can pay for it. `loot` opens the body, takes the listed slots and any money, then closes. `stop` sends `attack_stop`. `stats` stands still. Every tick's text already includes gold as `Ng Ns Nc (total copper)`, plus level, hp, power, map, and position.

`navigation_probe`, `path_preview`, `terrain_map`, and `observe_map` are client calls for when a move is stuck. `scripts/wow.py` does not wrap them. See the repository README for the calls and their rate limits. `face` takes a guid or an orientation, not both.

**6. Record a session for local training:**

`bot_mode.py` does not record unless `"record": true` or `COWCRAFT_RECORD=1`. When that is set, it appends one JSON row per tick to `~/.cowcraft/training/`. That directory is outside the git checkout. `*.jsonl` is gitignored, so a recording left inside the repo is not committed. Rows keep the observation summary, optional decision advice, and the action the main model took. Passwords and connection tokens are dropped. `"record": false` stays off even if the environment variable is set.

```bash
python3 recorder.py stats --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl
python3 recorder.py replay --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl --step
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
  asks already-loaded `d1-3b` and `d1-omni-600m` for advice. That advice is not required. The main model chooses the action. RAM stays safe.
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
