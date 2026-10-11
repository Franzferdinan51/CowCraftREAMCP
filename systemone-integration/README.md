# SystemOne × Minta integration

**Brain + hands.** SystemOne is the brain (model routing, typed decisions,
See > Decide > Act loop). The Minta MCP gateway is the hands (game control).
This layer wires them together so agents think via SystemOne and act in
World of Warcraft through the gateway.

```
Agent tick:
  SEE     CowCraftEnv.observe()  -> gateway observe -> text render
  DECIDE  judge(state)           -> advice plus a choice; advice is not required
  ACT     CowCraftEnv.act(choice) -> move, listed spell, or loot window
```

## Files

| File | What |
|---|---|
| `cowcraft_env.py` | SystemOne `Env` over the Minta gateway. Discrete actions (`move_north/south/east/west`, `attack`, `cast`, `stop`, `interact`, `loot`, `wait`) become gateway `action()` calls. `attack` and `cast` also cast one affordable spell the server listed. `loot` opens the corpse, takes listed slots and money, then closes. |
| `judge.py` | `SystemOneShimJudge` (live shim HTTP) + `StubJudge` (offline heuristic). `make_judge()` prefers the shim, falls back to stub. `build_questions()` builds the action/stuck/progress question set. |
| `minta_agent.py` | Single-agent runner. Uses SystemOne's `DecisionLoop` when the `systemone` package is importable, else a built-in See > Decide > Act loop with the same uncertainty gate. |
| `swarm.py` | Multi-agent crew. One thread per agent, own gateway session/account, own model routing, shared file-backed blackboard for coordination. |
| `swarm_config.example.json` | Example 3-agent crew (tank/scout/support) with per-agent model routing. |

## Quick start

1. Start the gateway (proxy mode = real CowCraft):
   ```bash
   cd ../mcp-gateway
   BACKEND=cowcraft python3 mcp_gateway.py --port 8901
   ```
2. (Optional) Start the SystemOne shim so the real brain is live:
   ```bash
   # from the SystemOne repo
   SYSTEMONE_SHIM_URL=http://127.0.0.1:8765 python3 -m systemone.shim
   ```
   Without it, `make_judge()` falls back to the offline `StubJudge`.
3. Run one agent:
   ```bash
   cd ../systemone-integration
   MINTA_USERNAME=Juno MINTA_PASSWORD='...' \
     python3 minta_agent.py --name Junoduck --role scout --ticks 40
   ```
4. Run the crew:
   ```bash
   cp swarm_config.example.json swarm_config.json  # edit accounts/models
   MINTA_PASSWORD='...' python3 swarm.py --config swarm_config.json
   # per-agent passwords: MINTA_PASSWORD_JUNO, MINTA_PASSWORD_DUCKCREW1, ...
   ```

## Model routing per agent

Each agent entry in the swarm config takes a `model` field, passed to the
SystemOne shim's decide call (the shim owns its registry; unknown ids are
ignored server-side). Example split:

`d1-3b` and `d1-omni-600m` advise what the character could do and what it could use. The advice is not required. A model that is not already loaded is not loaded.

`SystemOneShimJudge` posts once and returns `advice` (`text`, `required: false`, `do`, `use`) plus `action.choice`. `minta_agent.py` and `swarm.py` still execute that `action.choice`. `hermes-integration/bot_mode.py` does not: there the main model's reply is the only action, and missing advice does not block the tick.

Without the shim, every agent on this path runs the stub judge (no model calls at all).

Navigation reads are not in the discrete action list. Call them on the client when a move is stuck: `navigation_probe`, `path_preview` with `x`/`y`/`z`, `terrain_map`, and `observe_map`. They share the pacing in `cowcraft_mcp.py` and do not move the character. `face` takes a guid or an orientation, not both.

## Extending the action set

The judge picks from `ACTIONS` in `cowcraft_env.py`. To add e.g. `follow`
or `cast_frostbolt`, add the name to `ACTIONS` and a branch in
`CowCraftEnv._dispatch()` mapping it to gateway `action()` calls. The
gateway validates against its 161 capabilities; unknown kinds are rejected
with a clear error.

## Credentials

Never committed. Agents read `MINTA_USERNAME` / `MINTA_PASSWORD` (single)
or `MINTA_PASSWORD_<USERNAME>` (swarm) from the environment. See the root
`.gitignore`.
