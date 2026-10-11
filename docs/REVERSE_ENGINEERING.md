# CowCraft MCP — Reverse Engineering Report

Probed again 2026-10-11 against `https://cowcraft.world/mcp`. The server is
`cowcraft` **v0.3.16**, protocol `2024-11-05`. The notes below from v0.3.10
still describe the transport. The live surface is now **15 tools** and
**161** connect capabilities.

v0.3.16 additions, from `initialize`, `tools/list`, and one unauthenticated
`connect` + `disconnect` (no account login):

- Tools `resume` and `observe_map`.
- `resume` requires `username`, `password`, and `model_name`. Optional
  `connection` and `character_guid`. A success returns a replacement
  connection; the previous token stops working and is not disconnected first.
- `observe_map` takes only `connection`. At most once every 5 seconds, and it
  shares the 1/second navigation-read slot. The body may be an image.
- `observe` dropped `items` and added `events_ack` (string). Acknowledgement
  is explicit.
- `action` accepts optional `request_id`.
- New action kinds: `navigation_probe` (no fields), `path_preview` (`x`,`y`,`z`),
  `terrain_map` (no fields), `item_open` (`bag`,`slot`), `item_read_lore`
  (`item_guid`, optional pages), `object_read_lore` (`guid`, optional pages).
  The three navigation reads share one call per second and do not move.
- `face` is exactly one of `guid` or `orientation`.
- Creation is Human race 1 or Orc race 2. Login is five attempts per account
  and twenty per IP per minute; this client still waits 60 seconds and honors
  `retry_after_ms`.
- `list_actions` without login returns `authentication_required`.

The v0.3.10 probe was deliberately light: a handful of
`initialize`/`tools/list`/`tools/call` requests, one `connect` + `disconnect`
cycle. No account credentials used; no authenticated calls made.

Companion files: `cowcraft_mcp.py` (client), `SKILL.md` (usage spec).

---

## 1. Transport & Protocol

| Property | Value |
|---|---|
| URL | `https://cowcraft.world/mcp` |
| Transport | **Streamable HTTP, single-shot** — one JSON-RPC message per HTTP POST, plain JSON response |
| SSE | Accepted via `Accept: text/event-stream`, but server returns `Content-Type: application/json` with a plain body in practice |
| Session header | **None required** — no `Mcp-Session-Id`; every request is stateless at HTTP level |
| Auth mechanism | `connection` token passed **inside tool arguments**, not HTTP headers |
| JSON-RPC | 2.0, `id` echoed |
| `notifications/initialized` | Not required (client never sends it; server tolerates) |

### Supported MCP methods

| Method | Status |
|---|---|
| `initialize` | ✅ returns `protocolVersion`, `capabilities: {tools: {listChanged: true}}`, `serverInfo: {name: "cowcraft", version: "0.3.16"}` |
| `ping` | ✅ returns `{}` |
| `tools/list` | ✅ 15 tools |
| `tools/call` | ✅ tool results wrapped in MCP `content[0].text` envelope containing JSON |
| `resources/list` | ❌ `-32601 Method not found` |
| `prompts/list` | ❌ `-32601 Method not found` |

This is a **minimal MCP surface**: tools only. No resources, no prompts, no
sampling, no session management.

### Two-layer error model

1. **JSON-RPC layer**: transport/protocol errors (`-32601`, HTTP 4xx/5xx).
2. **Game layer**: tool calls return HTTP 200 with
   `{"error": {"code": "...", "message": "..."}}` inside the content text.
   Observed codes: `account_busy`, `rate_limited`, `invalid_arguments`.

### Connection token format

```
^[a-f0-9-]{36}\.[a-f0-9]{64}$
```

UUIDv4 + 64 hex chars — i.e. a session UUID with an HMAC-SHA256-style
signature. Self-validating; the server doesn't need a session store lookup
for authenticity (though it clearly keeps server-side lease state).

### Rate limits & reliability notes

- `observe`: max **1/sec**, budget **shared with `heartbeat`**
- `action`: max **20/sec per account**; "shared relay limits may also apply"
- `navigation_probe`, `path_preview`, and `terrain_map`: **1/sec** shared, and they do not move. `observe_map` is **once per 5 seconds** and also uses that slot
- `login`: five attempts per account and twenty per IP per minute. This client still waits 60 seconds. `rate_limited` carries
  a machine-readable `retry_after_ms` field. Tool descriptions repeatedly say:
  *"Honor retry_after_ms; never busy-loop."*
- **Serial calls enforced per account** — concurrent requests on one
  connection error out. One in-flight request at a time.
- Every tool description carries: *"Lease clock on results/errors; native
  timeout 10s"* — the lease countdown (re)starts when a call returns, and the
  native game backend is given 10 s per call.
- Transport is **flaky under load**: frequent `IncompleteRead` /
  `RemoteDisconnected`, especially on large responses (`tools/list` ~48 KB
  broke mid-stream on most attempts). Retry with backoff; never busy-loop.

---

## 2. Tool Reference (15 tools)

`resume` and `observe_map` are specified in the v0.3.16 delta at the top.
The per-tool notes below were first captured at v0.3.10 and still apply
except where that delta changes them.

### `connect`
Create a game connection lease. No auth needed.

- **required**: `model_name` (string)
- **returns**: `connection` (token), `lease`, `transport: "direct-server"`,
  `capabilities` (array of 161 action-kind names),
  `capabilities_confirmed: true`, `instructions` (usage/anti-cheat text)
- Full response keys: `lease`, `connection`, `transport`, `capabilities`,
  `capabilities_confirmed`, `instructions`

### `register`
Create an ordinary player account. *"No email, recovery or guest claiming.
Credentials stay private."*

- **required**: `connection`, `username`, `password` (10+ chars per SKILL.md)

### `login`
Sign in. Same shape/notes as `register`.

- **required**: `connection`, `username`, `password`
- Failure while another lease holds the account:
  `account_busy: "This account already has a direct agent lease; …"`

### `characters`
List the account's characters **and available creation options**.

- **required**: `connection`

### `create_character`
Create a level-1 **Human (race 1) or Orc (race 2)**. Both factions may
exist on one account. Names must be unique (2–12 letters).

- **args**: `connection` + `character: {name, race, class, gender, skin, face,
  hair_style, hair_color, facial_hair}`
- Warrior (class 1) confirmed working; Hunter (3) was rejected server-side.

### `delete_character`
Destructive. Character must be offline.

- **required**: `connection`, `guid`, `confirm_name` (must match exactly,
  case-insensitive); optional `request_id` (idempotency key)

### `select_character`
Enter the world.

- **required**: `connection`, `guid` (integer; the wrapper's string form
  caused `invalid_arguments` — send a JSON number)
- **Anti-cheat binding**: *"First MCP entry requires level 1 and no Player
  Tools history; already-bound characters retain ordinary earned levels.
  **Agent-bound characters cannot use Player Tools through any client**"* —
  the flag persists server-side and is enforced even in the browser client.
- **Model label**: `connect`'s `model_name` is the agent label. The first
  successful `select_character` permanently binds the character to that
  exact string. A later select that presents a different `model_name`
  fails with `benchmark_selection_rejected`. Upstream has no unbind,
  relabel, or admin clear — the original label is the only one that can
  select the character again. Do not retry a rejection in a loop. A
  character already bound to another label (the failure mode behind a
  stuck guid) stays unusable.

### `observe`
Bounded, player-visible structured state.

- **required**: `connection`
- **optional**: `sections` (e.g. self, nearby units/objects, quests, casting,
  auras, UI, events, combat), `items`, `quest_id`, `spell_id`,
  `spell_offset`/`spell_limit`, `inventory_offset`/`inventory_limit`
- Max 1/sec, shared with `heartbeat`.
- Returns `lease`, `self` (position, health, etc.), `nearby_units`,
  `nearby_objects`, `quests`, `auras`, `combat_ui`, …
- Unit fields include `guid`, `name`, `level`, `health/max_health`, `x/y/z`,
  `faction`, `hostile`, `dead`, `npc_flags`, `victim_guid`.
  **Do not use `npc_flags == 0` as "is player"** — hostile mobs also report 0.
  Use `hostile` + known player GUIDs/names + `victim_guid`.

### `heartbeat`
Renew the lease without returning state. *"A client harness may call every
30–60s while a model thinks."* Shares the 1/s observation budget.

- **required**: `connection`

### `action`
Submit one typed game action.

- **args**: `connection` + `action: {kind, ...kind-specific params}`
- `additionalProperties: false` on every variant — unknown fields rejected.
- Max 20/sec per account.
- Explicitly **unavailable** to agents: *"Player Tools, instant levelling,
  map teleport, custom mounts and **all chat** and human-written mail/letters"*
  — agents **cannot send chat messages**.
- Per-kind schemas: 156 native kinds in a nested `anyOf` (v0.3.16). `face`
  is two branches. Connect reports 161 capabilities, including the gateway
  meta kinds. 68 were captured below; the rest follow the same pattern
  (kind const + typed params). Optional `request_id` sits beside `action`.
- `list_actions` returns the *"authenticated, native-confirmed action
  names"* — the server confirms which kinds your session may use. Optional
  `family`/`kind` narrows the listing.

### `stop`
Stop pending movement/combat.

- **args**: `connection` (schema tail not captured; usage confirmed)

### `disconnect`
Close the control lease.

- **args**: `connection` (schema tail not captured; usage confirmed)

---

## 3. Action Catalog

v0.3.10 returned 155 names. v0.3.16 returns 161. The six added names are
`item_open`, `item_read_lore`, `object_read_lore`, `navigation_probe`,
`path_preview`, and `terrain_map`. The v0.3.10 list:

```
characters, create, delete_character, select, observe,
move, target, attack, attack_stop, cast, interact,
quest_accept, quest_details, quest_complete, quest_request_reward,
quest_reward, quest_abandon, quest_cancel, quest_confirm,
quest_share, quest_share_accept, quest_share_decline,
gossip_select, loot_open, loot_take, loot_money, loot_close,
loot_roll, loot_master_give,
trade_request, trade_begin, trade_accept, trade_cancel,
trade_item, trade_gold, trade_clear_item, trade_unaccept,
auction_search, auction_owned, auction_bids, auction_bid,
auction_sell, auction_cancel,
group_invite, group_accept, group_decline, group_leave,
group_kick, group_leader, group_loot_method,
raid_convert, raid_assistant, raid_subgroup, raid_subgroup_swap,
raid_target, raid_target_list, raid_ready_check, raid_ready_response,
trainer_list, trainer_learn, vendor_list, vendor_buy, vendor_sell,
vendor_buyback, vendor_repair,
item_use, item_equip, item_move, item_split, item_destroy,
item_equip_slot, set_ammo, action_button,
bank_open, bank_list, bank_buy_slot, bank_deposit, bank_withdraw,
mail_list, mail_mark_read, mail_take_item, mail_take_money,
mail_return, mail_delete,
release_spirit, reclaim_corpse, self_resurrect, resurrect_response,
spirit_healer, spirit_healer_query, spirit_healer_queue,
dismount, interact_object, emote, stand_state, face, jump,
duel_request, duel_accept, duel_cancel,
social_list, friend_add, friend_remove, ignore_add, ignore_remove,
pvp_toggle, area_trigger,
guild_query, guild_roster, guild_invite, guild_accept, guild_decline,
guild_leave, guild_remove, guild_promote, guild_demote,
guild_set_leader, guild_disband, guild_rank, guild_add_rank,
guild_delete_rank,
charter_list, charter_buy, charter_signatures, charter_query,
charter_rename, charter_offer, charter_sign, charter_decline,
charter_turn_in,
talent_learn, talent_reset, skill_unlearn,
cast_cancel, channel_cancel, autorepeat_stop, aura_cancel,
pet_command, pet_cast, pet_autocast,
taxi_list, taxi_take, taxi_route, bind_home,
transport_board, transport_leave,
battleground_list, battleground_join, battleground_status,
battleground_port, battleground_leave,
battleground_spirit_query, battleground_spirit_queue,
instance_reset, instance_info
```

### Schematized variants (68 captured from `tools/list`)

Format: `kind: required=[...] params=[...]`

**Movement/combat**
- `move`: required=[kind,x,y,z] — x/y/z numbers, ±100000
- `target` / `attack`: required=[kind,guid] — guid pattern `^[0-9]{1,20}$`
- `attack_stop`, `autorepeat_stop`, `dismount`, `release_spirit`: required=[kind]
- `cast`: params=[spell_id, target_guid, target_position{x,y,z}] (+ optional extras)
- `cast_cancel` / `channel_cancel` / `aura_cancel`: required=[kind, spell_id]

**Interaction**
- `interact` / `interact_object`: required=[kind,guid]
- `gossip_select`: required=[kind,guid,option_index], optional `code` (text input)
- `reclaim_corpse`: required=[kind,guid]

**Loot** — `loot_open`: [kind,guid] · `loot_take`: [kind,slot] ·
`loot_money`: [kind] · `loot_close`: [kind,guid] *(schema says guid;
in practice the server accepted it without — be lenient in clients)*

**Quests** — `quest_details`/`quest_accept`/`quest_complete`/
`quest_request_reward`: [kind,guid,quest_id] · `quest_reward`:
[kind,guid,quest_id,reward_index] · `quest_abandon`/`quest_confirm`:
[kind,quest_id] · `quest_cancel`: [kind]

**Group** — `group_invite`: [kind,name] · `group_accept`/`group_decline`/
`group_leave`: [kind]

**Trade** — `trade_request`: [kind,guid] · `trade_begin`/`trade_unaccept`/
`trade_cancel`: [kind] · `trade_accept`: [kind,offer_revision] (anti-desync) ·
`trade_item`: [kind,trade_slot,bag,slot] · `trade_clear_item`:
[kind,trade_slot] · `trade_gold`: [kind,copper]

**Vendor/trainer** — `trainer_list`/`vendor_list`: [kind,guid] ·
`trainer_learn`: [kind,guid,spell_id] · `vendor_buy`:
[kind,guid,item_id,count] · `vendor_sell`: [kind,guid,item_guid,count]

**Items** — `item_equip`/`item_use`: [kind,bag,slot] (+ optional
spell_index/target_* for use) · `item_move`: [bag,slot] (+source) ·
`item_split`: [kind,source,destination,count] · `item_destroy`:
[kind,location] (+count) · `item_equip_slot`: [kind,item_guid,equipment_slot] ·
`set_ammo`: [kind,item_id]

**Bank** — `bank_open`/`bank_buy_slot`: [kind,banker_guid] ·
`bank_list`: [kind,banker_guid] (+offset) ·
`bank_deposit`/`bank_withdraw`: [kind,banker_guid,location]

**Mail** — `mail_list`: [kind,mailbox_guid] ·
`mail_mark_read`/`mail_take_item`/`mail_take_money`/`mail_return`:
[kind,mailbox_guid,mail_id] · `mail_delete`: [kind,mailbox_guid,mail_id]
(+confirm_loss)

**Auction** — `auction_search`/`auction_owned`/`auction_bids`:
[kind,auctioneer_guid] (+query/offset for search) ·
`auction_bid`: [kind,auctioneer_guid,auction_id,price] ·
`auction_sell`: [kind,auctioneer_guid,item_guid,bid,buyout,duration_minutes] ·
`auction_cancel`: [kind,auctioneer_guid,auction_id]

Remaining ~87 kinds (spirit_healer, taxi, battleground, guild, pet, raid,
charter, talent, etc.) follow the identical schema pattern; their names are
in the catalog above.

---

## 4. Session / Lease Lifecycle

```
                    ┌──────────┐
                    │ initialize│  (MCP handshake, no auth)
                    └────┬─────┘
                         │ tools/call connect {model_name}
                         ▼
              ┌──────────────────────┐
              │  PREAUTH lease       │  seconds_remaining: 0, idle_timeout: 0
              │  (clock starts on    │  "Lease clock on results/errors"
              │   first result)      │
              └──────┬───────────────┘
                     │ register / login {username, password}
                     ▼
              ┌──────────────────────┐
              │  AUTHENTICATED       │  ~600 s lease (pre-world)
              │  (account bound)     │
              └──────┬───────────────┘
                     │ select_character {guid}
                     ▼
              ┌──────────────────────┐
              │  IN WORLD            │  ~120 s lease
              │  (character bound,   │  heartbeat/observe renew
              │   agent-flagged)     │
              └──────┬───────────────┘
                     │ disconnect  OR  lease expiry
                     ▼
                   (lease released;
                    expiry → reconnect + re-login required)
```

Key behaviors (observed + documented):

- **One lease per account**: a second `login` while a lease is live fails
  with `account_busy: "This account already has a direct agent lease"`.
- **Stale leases linger**: a dead client's lease was observed held for 1+ hr
  with no reaping. `disconnect` with the old token releases it — keep tokens.
- **Lease clock**: starts/restarts on every tool result or error
  ("Lease clock on results/errors"). Pre-auth `connect` returns
  `seconds_remaining: 0` — the countdown begins with the first subsequent
  call's result.
- **Renewal**: `heartbeat()` (no state) or `observe()` (returns state); every
  ~30–60 s is the documented cadence. Both draw from the same 1/sec budget.
- **Expiry recovery**: `resume` with the same account and `model_name`
  replaces the connection when the world lease is still live. `resumed: false`
  means that session is gone; then `connect` → `login` → `select_character`.
- **Rate-limit backoff**: `retry_after_ms` on `rate_limited`; never busy-loop.

---

## 5. Architecture (inferred)

```
 ┌──────────┐   HTTPS/JSON-RPC   ┌─────────────────────┐
 │ AI agent │ ─────────────────▶ │   MCP gateway       │
 │ (Python) │   single-shot POST │   (cowcraft v0.3.16)│
 └──────────┘                    │  - token leases     │
                                 │  - rate limiting    │
                                 │  - serial per-acct  │
                                 │  - 161 action       │
                                 │    schemas          │
                                 └─────────┬───────────┘
                                           │ "direct-server"
                                           │ "native" backend (10 s timeout)
                                           ▼
                                 ┌─────────────────────┐
                                 │  CMaNGOS worldserver│
                                 │  + custom bridge    │
                                 │  (session injection,│
                                 │   agent-bound flag) │
                                 └─────────────────────┘
```

Evidence for each piece:

- **`transport: "direct-server"`** (in `connect` response): the MCP gateway
  talks to the game server process directly — not by relaying through a
  logged-in player client.
- **"native-confirmed" / "native timeout 10s"**: there is a *native*
  (almost certainly C++) component bridging the gateway and the world
  server — likely a mangosd module or a sidecar speaking the world's
  internal protocol. Each action is confirmed against the live world
  ("native-confirmed action names").
- **Real game sessions**: agent characters are visible to ordinary players
  in-world (Duckets could see Junoduck). Each lease maps to a genuine
  logged-in session, not a bot API overlay.
- **Agent-bound flag**: `select_character` binds the character on first MCP
  entry (requires level 1 + no Player Tools history). The flag lives
  server-side (DB) and disables "Player Tools" (their GM/dev toolset) for
  that character **across all clients, including the browser client**.
  This is their anti-cheat: agents get ordinary-player powers only.
- **benilla** (their Rust/Bevy web client) is a *separate* control plane for
  humans; the MCP gateway is a parallel API into the same worldserver.
- **Chat/mail-writing disabled** for agents at the gateway schema level
  (not just policy) — reduces abuse/spam surface.

### What "Player Tools" implies

First-entry requirements (level 1, no Player Tools history) + a persistent
per-character restriction enforced in every client strongly suggests a
`character`-row boolean (e.g. `is_agent_bound`) checked in the worldserver's
command/telemetry path. Replication: add a column + gate GM/dev commands on it.

---

## 6. Replication Guide

To build a cowcraft-equivalent ("I run the server, my agent plays"):

### Components

| # | Component | Options | Notes |
|---|---|---|---|
| 1 | Vanilla WoW server | **CMaNGOS** (classic) | mangosd + realmd + MySQL/MariaDB; 1.12.1 client data for maps |
| 2 | Native bridge | Custom mangosd **module** (C++) exposing session inject + state snapshot | Hardest part. Alternative: **HermesProxy**-style (WowLegacyCore) connection proxy, or drive a **headless benilla-derived client** per agent session |
| 3 | MCP gateway | Any language; must implement `initialize`, `ping`, `tools/list`, `tools/call` over single-shot HTTPS POST | Stateless HTTP; token = `UUID.HMAC-SHA256` |
| 4 | Lease manager | Token issuance, per-account serial queue, expiry timers, `retry_after_ms` backoff | Reap stale leases aggressively (cowcraft's doesn't — don't copy that bug) |
| 5 | Action schema | 161 capabilities, `additionalProperties: false`, per-kind `const` discriminator | Copy the catalog in §3 plus the six v0.3.16 kinds; validate strictly |
| 6 | Observe snapshot | Bounded world-state serializer (self, nearby units/objects, quests, auras, combat UI) | 1/sec budget; `sections` param for scoping |
| 7 | Agent-bound flag | DB column on character + enforcement in command path | Level-1 + no-history gate on first bind |
| 8 | Rate limiting | 1/s observe, 20/s actions, 1/s navigation reads, 1/5s observe_map, login backoff | |

### Effort estimate

- **MCP gateway + schemas + leases**: days (straightforward; this report +
  `cowcraft_mcp.py` are a near-complete spec).
- **Native bridge (real sessions)**: **weeks** — this is the actual moat.
  Requires C++ work inside/around mangosd or a robust headless-client
  farm (one game client process per agent, driven via its own API).
- **Anti-cheat binding**: days (DB flag + command gating).
- **Hardening** (serial queues, backoff, snapshot bounds): days.

### Don't-copy list (observed weaknesses)

1. Stale leases are never reaped (1+ hr ghost holds `account_busy`).
2. Large `tools/list` responses routinely break mid-stream (chunked
   encoding dies ~27–48 KB) — paginate or compress.
3. No `retry_after_ms` on transport-level failures, only on login.

### Privacy posture (from their site/docs)

Stores: username, salted password hash, IP (90 days), characters, audit log.
Chat messages are NOT logged. "Nothing shared, nothing sold."

---

## 7. Quick Reference: Request Shapes

```http
POST https://cowcraft.world/mcp
Content-Type: application/json
Accept: application/json, text/event-stream

{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "protocolVersion":"2024-11-05","capabilities":{},
  "clientInfo":{"name":"probe","version":"1.0"}}}
```

```json
// tools/call connect → 200 OK
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
  "name":"connect","arguments":{"model_name":"my-agent"}}}
// result.content[0].text =
{"connection":"<uuid>.<hmac>","lease":{...},"transport":"direct-server",
 "capabilities":[...161...],"capabilities_confirmed":true,"instructions":"..."}
```

```json
// tools/call action
{"name":"action","arguments":{
  "connection":"<uuid>.<hmac>",
  "action":{"kind":"move","x":-8940,"y":-130,"z":83}}}
```

```json
// game-level error (HTTP 200, inside content text)
{"error":{"code":"account_busy","message":"This account already has a direct agent lease; ..."}}
```
