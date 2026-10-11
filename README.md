# CowCraftREAMCP

Self-hosted vanilla WoW (1.12.1) MCP stack for AI agents, reverse-engineered
from [cowcraft.world](https://cowcraft.world). The live target is
[https://cowcraft.world/mcp](https://cowcraft.world/mcp) (server 0.3.16):
15 tools and 161 actions. CMaNGOS stays optional and is not wired up.

Already-loaded local models may advise. The main model chooses the action.
That advice is not required, and a missing advisor does not block the tick.
The Minta gateway is what talks to the game.

```
Agent tick:
  SEE     observe, plus a navigation read or observe_map when you need the ground
  DECIDE  d1-3b and d1-omni-600m may advise; the main model replies with one action
  ACT     a move, a spell the server listed, or the open loot window
```

## Layout

| Path | What |
|---|---|
| `mcp-gateway/` | CowCraft-protocol MCP server and the `CowCraft` client. Backends: `stub` (fake world), `cowcraft` (proxy to cowcraft.world), `live` (optional CMaNGOS, unused). Stdlib Python only. |
| `systemone-integration/` | `CowCraftEnv` (move, attack, cast, loot), judges, one agent, and a swarm. |
| `hermes-integration/` | Headless `bot_mode.py`, the Hermes skill, swarm coordinator, and private training logs. |
| `cmangos/` | Build notes for a WoW server. Not the current target. |
| `docs/` | `REVERSE_ENGINEERING.md` (protocol spec), `MINTA_README.md` (build order). |

Not in this repo:

- **benilla** web client — `git clone https://github.com/samwhosung/benilla`
- **SystemOne** — `git clone https://github.com/Franzferdinan51/SystemOne`
- **WoW 1.12.1 game data** — user-supplied, never committed

## Start the gateway

```sh
cd mcp-gateway
export MINTA_HMAC_SECRET="$(openssl rand -hex 32)"   # stable across restarts
BACKEND=cowcraft python3 mcp_gateway.py --port 8901
```

`BACKEND=stub` exercises the protocol with no game server. Point a client at
`http://127.0.0.1:8901/mcp`. Health is `/health`. Upstream override:
`MINTA_COWCRAFT_URL` (default `https://cowcraft.world/mcp`).

The gateway holds one upstream session per account. Login credentials arrive
on the `login` tool and stay in memory. The `resume` tool result omits the
new `connection` token; the gateway keeps that token. The direct client
below returns it.

## Drive one character

Install the Hermes skill, then use the one-shot helper. The gateway must
already be running.

```sh
cp -r hermes-integration/hermes_skill ~/.hermes/skills/cowcraft-wow
cd hermes-integration/hermes_skill
export MINTA_USERNAME='your-account'
export MINTA_PASSWORD='...'
export MINTA_GATEWAY_URL=http://127.0.0.1:8901/mcp
python3 scripts/wow.py observe --character Yourchar
python3 scripts/wow.py move --character Yourchar --dir north --steps 2
python3 scripts/wow.py attack --character Yourchar
python3 scripts/wow.py loot --character Yourchar
python3 scripts/wow.py interact --character Yourchar
python3 scripts/wow.py stop --character Yourchar
```

`attack` targets the nearest hostile and casts one spell the server listed
when current power can pay for it. Spell names `attack` and `shoot` are
skipped. The client does not invent spell ids. `loot` opens the corpse,
reads the `ui` section, takes listed slots and any money, then closes.
`stop` sends `attack_stop`.

## Headless bot

```sh
cd hermes-integration
cp bot.json.example mybot.json    # username, character name, role
MINTA_PASSWORD='...' python3 bot_mode.py --config mybot.json
curl -s http://127.0.0.1:8891/health
```

Stop it with SIGINT or SIGTERM. Each tick observes, asks for optional
advice, and executes only the main model's reply. No loaded main model
means `wait`. Config keys that matter:

| Key | Meaning |
|---|---|
| `username`, `password` | The caller's account. An empty password reads `MINTA_PASSWORD`. |
| `name` | Character name. |
| `model_name` | Agent label sent to `connect`. Reuse the same label for that character. Default `bot-<name>`. This is not an LLM id. |
| `gateway_url` | Default `http://127.0.0.1:8901/mcp`. |
| `tick_seconds` | Pause between ticks. Default 5. Keep it at or above the observe limit. |
| `health_port` | Default in the example is 8891. |
| `record` | Private training rows. Off unless `true`. |

Per-account lease files live under `MINTA_COWCRAFT_STATE`
(default `/tmp/cowcraft-accounts/<account>/`). `connection.txt` is the token
only. `model.txt` is the agent label.

## Direct client

Use this when you want the raw tools, including the navigation reads the
shell helper does not wrap. Run it from `mcp-gateway/` so the import works.

```python
from cowcraft_mcp import CowCraft

game = CowCraft("https://cowcraft.world/mcp")  # or http://127.0.0.1:8901/mcp
game.connect("my-agent")                       # stable agent label
game.login("your-account", "your-password")    # at most once per 60 seconds
for character in game.characters().get("characters", []):
    print(character["name"], character["guid"])
game.select_character(1234)                    # JSON integer guid
state = game.observe()
me = state["self"]
game.action("move", {"x": me["x"], "y": me["y"] + 2, "z": me["z"]})
game.disconnect()
```

`connect` calls `initialize` first. On the live server, `disconnect` releases
the lease only after `initialize`. Passwords are never written to the token
file. Default files are `/tmp/cowcraft_conn.txt` and `/tmp/cowcraft_model.txt`.

### Recover a lease

A saved token for this account is reused. Replacing it copies the old token
to `connection.txt.bak` (or `/tmp/cowcraft_conn.txt.bak`). That backup has
no password. A later process can still release it:

```python
from cowcraft_mcp import CowCraft
game = CowCraft(token_path="/tmp/cowcraft_conn.txt.bak")
game.initialize()
game.disconnect()   # do not login in this process
```

`account_busy` means another session holds the account. It is not
`rate_limited`. Wait and try once. `resume` replaces a dropped token. Send
the same username, password, and `model_name`. Leave the old token for the
server to retire.

```python
game.resume("your-account", "your-password", "my-agent")
```

`invalid_connection`, `lease_expired`, and `not_authenticated` are dead
leases. Those are the cases where a new login is appropriate. Honor
`retry_after_ms` on `rate_limited`.

### Look around

`observe` and `heartbeat` share one call per second. Ask for the pages you
need. Send `events_ack` only for events you have already processed.

```python
game.observe(sections=["self", "nearby_units", "ui"],
             spell_limit=40, inventory_limit=20)
game.heartbeat()
```

These reads do not move the character. `navigation_probe`, `path_preview`,
and `terrain_map` share one call per second. `observe_map` is at most once
every 5 seconds and also uses that navigation slot. The map result is a
terrain image, not a camera screenshot.

```python
game.action("navigation_probe")
game.action("path_preview", {"x": me["x"] + 10, "y": me["y"], "z": me["z"]})
game.action("terrain_map")
game.observe_map()
game.action("face", {"guid": 456})            # guid or orientation, not both
game.action("face", {"orientation": 1.57})
```

`list_actions` returns the authenticated names. Filter with `family` or `kind`.

### Cast and loot

Cast only a spell id the latest observe listed, and only when power can pay
the listed cost.

```python
game.action("target", {"guid": hostile_guid})
game.action("cast", {"spell_id": spell_id, "target_guid": hostile_guid})
game.action("attack", {"guid": hostile_guid})
game.action("attack_stop")                    # break off combat
```

```python
game.action("loot_open", {"guid": corpse_guid})
window = game.observe(sections=["self", "nearby_units", "ui"])
game.action("loot_money")
game.action("loot_take", {"slot": 0})
game.action("loot_close", {"guid": corpse_guid})
```

Vendor sell is `vendor_sell` with `guid`, `item_guid`, and `count`.

## Decision advice

`hermes-integration/bot_mode.py` is the path that treats advice as optional.

1. If `d1-3b` or `d1-omni-600m` is already loaded in LM Studio, each one is asked what the character could do and what it could use.
2. The text is prefixed `Decision advice, not required.`
3. The first loaded model that is neither of those ids is the main model. Its reply is the action: `move_north`, `move_south`, `move_east`, `move_west`, `attack`, `cast`, `stop`, `interact`, `loot`, or `wait`.
4. No main model, an empty model list, or a reply that is not one of those words becomes `wait`. Advice is never promoted to a move.
5. This code does not load a model. `PREFER_MODEL` is ignored.

`LMSTUDIO_URL` defaults to `http://100.68.208.113:1234`. The key file
defaults to `~/.config/lmstudio/api_key`.

The SystemOne shim (`systemone-integration/judge.py`) returns the same
advice with `required: false`. `minta_agent.py` and `swarm.py` still execute
that response's `action.choice`. Use `bot_mode.py` when the main model must
be free to ignore the advice.

## Private training rows

Off unless you opt in. Set `"record": true` in the bot config, or
`COWCRAFT_RECORD=1`. `"record": false` stays off even when the variable is
set.

Rows append to `~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl` (directory
`0700`, file `0600`). Each row has the observation summary, the advice text,
`advice_required: false`, the action taken, confidence, model id, and
whether the tick progressed. Passwords, tokens, and connection fields are
removed. A `record_path` inside this repo is redirected to that home
directory. `*.jsonl` and `training/` are gitignored.

```sh
cd hermes-integration
python3 recorder.py stats --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl
python3 recorder.py replay --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl --step
```

## Characters

`create_character` accepts Human race `1` and Orc race `2`. Other races are
rejected locally and not sent. Appearance fields `skin`, `face`,
`hair_style`, `hair_color`, and `facial_hair` are integers. The name is 2–12
letters. `select_character` takes an integer guid. On
`benchmark_selection_rejected`, do not retry that character.

First MCP entry marks the character agent-bound. Reuse the same `model_name`
on later visits.

## Rate limits

The client sleeps for these floors. Also wait for any `retry_after_ms` the
server returns.

| Call | Limit |
|---|---|
| `observe`, `heartbeat` | 1 per second, shared |
| `action` | 20 per second, serial per account |
| `navigation_probe`, `path_preview`, `terrain_map` | 1 per second, shared, no movement |
| `observe_map` | 1 per 5 seconds, and it uses the navigation slot |
| `login` | 1 per 60 seconds in this client. The server also allows five per account and twenty per IP each minute |
| `register` | five attempts per IP per five minutes |

Calls for one account are serial. Agents cannot use chat, teleports, Player
Tools, or instant levelling.

## Docs

- `docs/REVERSE_ENGINEERING.md` — protocol spec, lease lifecycle, action schemas.
- `mcp-gateway/README.md` — backends and env vars.
- `systemone-integration/README.md` — env, judges, swarm.
- `hermes-integration/README.md` — bot, skill install, swarm coordinator.
- `hermes-integration/hermes_skill/references/gateway-protocol.md` — the 15 tools.
- `cmangos/BUILD.md` — WoW server build notes.

## Status

- [x] MCP protocol replicated for cowcraft.world 0.3.16 (15 tools, 161 actions)
- [x] Proxy backend, saved-lease resume, and token backup outside the repo
- [x] Spells from the server spell list, and loot of the open window
- [x] Navigation reads and `observe_map`, paced on the client
- [x] Optional decision advice from already-loaded `d1-3b` and `d1-omni-600m`
- [x] Private training rows, off unless opted in
- [ ] Live CMaNGOS backend (`live_backend.WoWBackendImpl`)
- [ ] benilla headless agent client

## License

Our code: MIT. benilla, SystemOne, and CMaNGOS keep their own licenses.
Not affiliated with Blizzard.
