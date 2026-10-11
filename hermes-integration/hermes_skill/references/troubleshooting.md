# Troubleshooting

## `account_busy` on login

Another session holds the account. This is not a rate limit. A saved
connection token for this account is resumed and must not be disconnected.
Wait and try login once. A later process releases only the backup copy.
Do not busy-loop login. A `benchmark_selection_rejected` character is bound to
a different agent label; do not retry it. Pass the original `model_name`,
or enter a different character.

## `Remote end closed connection` / `IncompleteRead`

CowCraft's transport is flaky on large responses. The proxy backend
(`cowcraft_backend.py`) retries with exponential backoff automatically.
If you call the gateway directly, retry the same call after 2–5s.

## `rate_limited`

You're calling too fast. Observe max 1/sec, actions max 20/sec. The
response includes `retry_after_ms` — honor it, don't busy-loop.

## Character creation rejected

- Name must be 2–12 letters (no numbers/spaces).
- Only Human (race=1) via MCP; Warrior (class 1) is confirmed working.
- One character per account is the tested path.

## Bot won't connect

1. Is the gateway running? `curl -s http://127.0.0.1:8901/mcp -X POST ...`
2. Are `MINTA_USERNAME` / `MINTA_PASSWORD` set (or in the bot JSON)?
3. Is CowCraft itself up? Try the skill's `observe` command manually.
4. Check the bot log (rotating, default `/tmp/<name>-bot.log`).

## Model not responding

`d1-3b` and `d1-omni-600m` only advise what to do and what to use. The
main model does not have to follow them. If they are not already loaded,
the tick continues with no advice. The bot does not load a model. Check
`http://100.68.208.113:1234/v1/models`.

## Health endpoint

Each bot serves `GET /health` on its `health_port` (default 8891):

```json
{"status": "running", "tick": 42, "last_action": "attack",
 "last_error": null, "uptime_s": 1337}
```

Poll it from a supervisor or from Hermes to watch the crew.
