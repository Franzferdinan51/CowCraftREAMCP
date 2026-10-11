"""
Action catalog for the Minta MCP gateway.

155 action kinds, replicated from CowCraft's `connect` response (v0.3.10).
Schemas follow the observed pattern: kind const discriminator + typed params,
additionalProperties: false on every variant.
"""

# Full capability list as returned by CowCraft's connect (155 entries)
CAPABILITIES = [
    "characters", "create", "delete_character", "select", "observe",
    "move", "target", "attack", "attack_stop", "cast", "interact",
    "quest_accept", "quest_details", "quest_complete", "quest_request_reward",
    "quest_reward", "quest_abandon", "quest_cancel", "quest_confirm",
    "quest_share", "quest_share_accept", "quest_share_decline",
    "gossip_select", "loot_open", "loot_take", "loot_money", "loot_close",
    "loot_roll", "loot_master_give",
    "trade_request", "trade_begin", "trade_accept", "trade_cancel",
    "trade_item", "trade_gold", "trade_clear_item", "trade_unaccept",
    "auction_search", "auction_owned", "auction_bids", "auction_bid",
    "auction_sell", "auction_cancel",
    "group_invite", "group_accept", "group_decline", "group_leave",
    "group_kick", "group_leader", "group_loot_method",
    "raid_convert", "raid_assistant", "raid_subgroup", "raid_subgroup_swap",
    "raid_target", "raid_target_list", "raid_ready_check", "raid_ready_response",
    "trainer_list", "trainer_learn", "vendor_list", "vendor_buy", "vendor_sell",
    "vendor_buyback", "vendor_repair",
    "item_use", "item_equip", "item_move", "item_split", "item_destroy",
    "item_equip_slot", "set_ammo", "action_button",
    "bank_open", "bank_list", "bank_buy_slot", "bank_deposit", "bank_withdraw",
    "mail_list", "mail_mark_read", "mail_take_item", "mail_take_money",
    "mail_return", "mail_delete",
    "release_spirit", "reclaim_corpse", "self_resurrect", "resurrect_response",
    "spirit_healer", "spirit_healer_query", "spirit_healer_queue",
    "dismount", "interact_object", "emote", "stand_state", "face", "jump",
    "duel_request", "duel_accept", "duel_cancel",
    "social_list", "friend_add", "friend_remove", "ignore_add", "ignore_remove",
    "pvp_toggle", "area_trigger",
    "guild_query", "guild_roster", "guild_invite", "guild_accept",
    "guild_decline", "guild_leave", "guild_remove", "guild_promote",
    "guild_demote", "guild_set_leader", "guild_disband", "guild_rank",
    "guild_add_rank", "guild_delete_rank",
    "charter_list", "charter_buy", "charter_signatures", "charter_query",
    "charter_rename", "charter_offer", "charter_sign", "charter_decline",
    "charter_turn_in",
    "talent_learn", "talent_reset", "skill_unlearn",
    "cast_cancel", "channel_cancel", "autorepeat_stop", "aura_cancel",
    "pet_command", "pet_cast", "pet_autocast",
    "taxi_list", "taxi_take", "taxi_route", "bind_home",
    "transport_board", "transport_leave",
    "battleground_list", "battleground_join", "battleground_status",
    "battleground_port", "battleground_leave",
    "battleground_spirit_query", "battleground_spirit_queue",
    "instance_reset", "instance_info",
]

# Per-kind parameter schemas: kind -> {"required": [...], "params": {name: type}}
# Types: "number", "integer", "string", "boolean", "guid", "position"
# "guid" = string matching ^[0-9]{1,20}$
ACTION_SCHEMAS = {
    # -- movement / combat --
    "move": {"required": ["kind", "x", "y", "z"],
             "params": {"x": "number", "y": "number", "z": "number"}},
    "target": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "attack": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "attack_stop": {"required": ["kind"], "params": {}},
    "cast": {"required": ["kind", "spell_id"],
             "params": {"spell_id": "integer", "target_guid": "guid",
                        "target_position": "position"}},
    "cast_cancel": {"required": ["kind", "spell_id"],
                    "params": {"spell_id": "integer"}},
    "channel_cancel": {"required": ["kind", "spell_id"],
                       "params": {"spell_id": "integer"}},
    "aura_cancel": {"required": ["kind", "spell_id"],
                    "params": {"spell_id": "integer"}},
    "autorepeat_stop": {"required": ["kind"], "params": {}},
    "dismount": {"required": ["kind"], "params": {}},
    "face": {"required": ["kind"], "params": {"angle": "number"}},
    "jump": {"required": ["kind"], "params": {}},
    "stand_state": {"required": ["kind"], "params": {"state": "integer"}},
    "emote": {"required": ["kind"], "params": {"emote_id": "integer"}},

    # -- interaction --
    "interact": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "interact_object": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "gossip_select": {"required": ["kind", "guid", "option_index"],
                      "params": {"guid": "guid", "option_index": "integer",
                                 "code": "string"}},
    "area_trigger": {"required": ["kind"], "params": {"trigger_id": "integer"}},

    # -- death --
    "release_spirit": {"required": ["kind"], "params": {}},
    "reclaim_corpse": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "self_resurrect": {"required": ["kind"], "params": {}},
    "resurrect_response": {"required": ["kind"],
                           "params": {"accept": "boolean"}},
    "spirit_healer": {"required": ["kind", "guid", "confirm"],
                      "params": {"guid": "guid", "confirm": "boolean"}},
    "spirit_healer_query": {"required": ["kind", "guid"],
                            "params": {"guid": "guid"}},
    "spirit_healer_queue": {"required": ["kind"], "params": {}},

    # -- loot --
    "loot_open": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "loot_take": {"required": ["kind", "slot"], "params": {"slot": "integer"}},
    "loot_money": {"required": ["kind"], "params": {}},
    "loot_close": {"required": ["kind"], "params": {"guid": "guid"}},
    "loot_roll": {"required": ["kind"], "params": {"slot": "integer"}},
    "loot_master_give": {"required": ["kind"],
                         "params": {"slot": "integer", "player_guid": "guid"}},

    # -- quests --
    "quest_details": {"required": ["kind", "guid", "quest_id"],
                      "params": {"guid": "guid", "quest_id": "integer"}},
    "quest_accept": {"required": ["kind", "guid", "quest_id"],
                     "params": {"guid": "guid", "quest_id": "integer"}},
    "quest_complete": {"required": ["kind", "guid", "quest_id"],
                       "params": {"guid": "guid", "quest_id": "integer"}},
    "quest_request_reward": {"required": ["kind", "guid", "quest_id"],
                             "params": {"guid": "guid", "quest_id": "integer"}},
    "quest_reward": {"required": ["kind", "guid", "quest_id", "reward_index"],
                     "params": {"guid": "guid", "quest_id": "integer",
                                "reward_index": "integer"}},
    "quest_abandon": {"required": ["kind", "quest_id"],
                      "params": {"quest_id": "integer"}},
    "quest_cancel": {"required": ["kind"], "params": {}},
    "quest_confirm": {"required": ["kind", "quest_id"],
                      "params": {"quest_id": "integer"}},
    "quest_share": {"required": ["kind", "quest_id"],
                    "params": {"quest_id": "integer"}},
    "quest_share_accept": {"required": ["kind"], "params": {}},
    "quest_share_decline": {"required": ["kind"], "params": {}},

    # -- group / raid --
    "group_invite": {"required": ["kind", "name"], "params": {"name": "string"}},
    "group_accept": {"required": ["kind"], "params": {}},
    "group_decline": {"required": ["kind"], "params": {}},
    "group_leave": {"required": ["kind"], "params": {}},
    "group_kick": {"required": ["kind", "name"], "params": {"name": "string"}},
    "group_leader": {"required": ["kind", "name"], "params": {"name": "string"}},
    "group_loot_method": {"required": ["kind", "method"],
                          "params": {"method": "integer"}},
    "raid_convert": {"required": ["kind"], "params": {}},
    "raid_assistant": {"required": ["kind", "name"],
                       "params": {"name": "string", "promote": "boolean"}},
    "raid_subgroup": {"required": ["kind", "name"],
                      "params": {"name": "string", "subgroup": "integer"}},
    "raid_subgroup_swap": {"required": ["kind"],
                           "params": {"name1": "string", "name2": "string"}},
    "raid_target": {"required": ["kind"],
                    "params": {"icon": "integer", "guid": "guid"}},
    "raid_target_list": {"required": ["kind"], "params": {}},
    "raid_ready_check": {"required": ["kind"], "params": {}},
    "raid_ready_response": {"required": ["kind"],
                            "params": {"ready": "boolean"}},

    # -- trade --
    "trade_request": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "trade_begin": {"required": ["kind"], "params": {}},
    "trade_accept": {"required": ["kind", "offer_revision"],
                     "params": {"offer_revision": "integer"}},
    "trade_cancel": {"required": ["kind"], "params": {}},
    "trade_unaccept": {"required": ["kind"], "params": {}},
    "trade_item": {"required": ["kind", "trade_slot", "bag", "slot"],
                   "params": {"trade_slot": "integer", "bag": "integer",
                              "slot": "integer"}},
    "trade_clear_item": {"required": ["kind", "trade_slot"],
                         "params": {"trade_slot": "integer"}},
    "trade_gold": {"required": ["kind", "copper"],
                   "params": {"copper": "integer"}},

    # -- vendor / trainer --
    "trainer_list": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "trainer_learn": {"required": ["kind", "guid", "spell_id"],
                      "params": {"guid": "guid", "spell_id": "integer"}},
    "vendor_list": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "vendor_buy": {"required": ["kind", "guid", "item_id", "count"],
                   "params": {"guid": "guid", "item_id": "integer",
                              "count": "integer"}},
    "vendor_sell": {"required": ["kind", "guid", "item_guid", "count"],
                    "params": {"guid": "guid", "item_guid": "guid",
                               "count": "integer"}},
    "vendor_buyback": {"required": ["kind", "guid", "slot"],
                       "params": {"guid": "guid", "slot": "integer"}},
    "vendor_repair": {"required": ["kind", "guid"],
                      "params": {"guid": "guid", "item_guid": "guid"}},

    # -- items --
    "item_use": {"required": ["kind", "bag", "slot"],
                 "params": {"bag": "integer", "slot": "integer",
                            "spell_index": "integer", "target_guid": "guid"}},
    "item_equip": {"required": ["kind", "bag", "slot"],
                   "params": {"bag": "integer", "slot": "integer"}},
    "item_move": {"required": ["kind", "bag", "slot"],
                  "params": {"bag": "integer", "slot": "integer",
                             "dest_bag": "integer", "dest_slot": "integer"}},
    "item_split": {"required": ["kind", "source", "destination", "count"],
                   "params": {"source": "string", "destination": "string",
                              "count": "integer"}},
    "item_destroy": {"required": ["kind", "location"],
                     "params": {"location": "string", "count": "integer"}},
    "item_equip_slot": {"required": ["kind", "item_guid", "equipment_slot"],
                        "params": {"item_guid": "guid",
                                   "equipment_slot": "integer"}},
    "set_ammo": {"required": ["kind", "item_id"],
                 "params": {"item_id": "integer"}},
    "action_button": {"required": ["kind"], "params": {"button": "integer"}},

    # -- bank --
    "bank_open": {"required": ["kind", "banker_guid"],
                  "params": {"banker_guid": "guid"}},
    "bank_list": {"required": ["kind", "banker_guid"],
                  "params": {"banker_guid": "guid", "offset": "integer"}},
    "bank_buy_slot": {"required": ["kind", "banker_guid"],
                      "params": {"banker_guid": "guid"}},
    "bank_deposit": {"required": ["kind", "banker_guid", "location"],
                     "params": {"banker_guid": "guid", "location": "string"}},
    "bank_withdraw": {"required": ["kind", "banker_guid", "location"],
                      "params": {"banker_guid": "guid", "location": "string"}},

    # -- mail --
    "mail_list": {"required": ["kind", "mailbox_guid"],
                  "params": {"mailbox_guid": "guid"}},
    "mail_mark_read": {"required": ["kind", "mailbox_guid", "mail_id"],
                       "params": {"mailbox_guid": "guid", "mail_id": "integer"}},
    "mail_take_item": {"required": ["kind", "mailbox_guid", "mail_id"],
                       "params": {"mailbox_guid": "guid", "mail_id": "integer"}},
    "mail_take_money": {"required": ["kind", "mailbox_guid", "mail_id"],
                        "params": {"mailbox_guid": "guid",
                                   "mail_id": "integer"}},
    "mail_return": {"required": ["kind", "mailbox_guid", "mail_id"],
                    "params": {"mailbox_guid": "guid", "mail_id": "integer"}},
    "mail_delete": {"required": ["kind", "mailbox_guid", "mail_id"],
                    "params": {"mailbox_guid": "guid", "mail_id": "integer",
                               "confirm_loss": "boolean"}},

    # -- auction --
    "auction_search": {"required": ["kind", "auctioneer_guid"],
                       "params": {"auctioneer_guid": "guid", "query": "string",
                                  "offset": "integer"}},
    "auction_owned": {"required": ["kind", "auctioneer_guid"],
                      "params": {"auctioneer_guid": "guid"}},
    "auction_bids": {"required": ["kind", "auctioneer_guid"],
                     "params": {"auctioneer_guid": "guid"}},
    "auction_bid": {"required": ["kind", "auctioneer_guid", "auction_id",
                                 "price"],
                    "params": {"auctioneer_guid": "guid",
                               "auction_id": "integer", "price": "integer"}},
    "auction_sell": {"required": ["kind", "auctioneer_guid", "item_guid",
                                  "bid", "buyout", "duration_minutes"],
                     "params": {"auctioneer_guid": "guid", "item_guid": "guid",
                                "bid": "integer", "buyout": "integer",
                                "duration_minutes": "integer"}},
    "auction_cancel": {"required": ["kind", "auctioneer_guid", "auction_id"],
                       "params": {"auctioneer_guid": "guid",
                                  "auction_id": "integer"}},

    # -- social --
    "social_list": {"required": ["kind"], "params": {}},
    "friend_add": {"required": ["kind", "name"], "params": {"name": "string"}},
    "friend_remove": {"required": ["kind", "name"],
                      "params": {"name": "string"}},
    "ignore_add": {"required": ["kind", "name"], "params": {"name": "string"}},
    "ignore_remove": {"required": ["kind", "name"],
                      "params": {"name": "string"}},
    "pvp_toggle": {"required": ["kind"], "params": {}},

    # -- duel --
    "duel_request": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "duel_accept": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "duel_cancel": {"required": ["kind"], "params": {}},

    # -- pet --
    "pet_command": {"required": ["kind"], "params": {"command": "string"}},
    "pet_cast": {"required": ["kind", "spell_id"],
                 "params": {"spell_id": "integer", "target_guid": "guid"}},
    "pet_autocast": {"required": ["kind", "spell_id"],
                     "params": {"spell_id": "integer", "enable": "boolean"}},

    # -- travel --
    "taxi_list": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "taxi_take": {"required": ["kind", "guid"],
                  "params": {"guid": "guid", "node_id": "integer"}},
    "taxi_route": {"required": ["kind", "guid"],
                   "params": {"guid": "guid"}},
    "bind_home": {"required": ["kind", "guid"], "params": {"guid": "guid"}},
    "transport_board": {"required": ["kind", "guid"],
                        "params": {"guid": "guid"}},
    "transport_leave": {"required": ["kind"], "params": {}},

    # -- battleground --
    "battleground_list": {"required": ["kind"], "params": {}},
    "battleground_join": {"required": ["kind"],
                          "params": {"bg_type": "integer"}},
    "battleground_status": {"required": ["kind"], "params": {}},
    "battleground_port": {"required": ["kind"], "params": {}},
    "battleground_leave": {"required": ["kind"], "params": {}},
    "battleground_spirit_query": {"required": ["kind"], "params": {}},
    "battleground_spirit_queue": {"required": ["kind"], "params": {}},

    # -- guild --
    "guild_query": {"required": ["kind"], "params": {}},
    "guild_roster": {"required": ["kind"], "params": {}},
    "guild_invite": {"required": ["kind", "name"],
                     "params": {"name": "string"}},
    "guild_accept": {"required": ["kind"], "params": {}},
    "guild_decline": {"required": ["kind"], "params": {}},
    "guild_leave": {"required": ["kind"], "params": {}},
    "guild_remove": {"required": ["kind", "name"],
                     "params": {"name": "string"}},
    "guild_promote": {"required": ["kind", "name"],
                      "params": {"name": "string"}},
    "guild_demote": {"required": ["kind", "name"],
                     "params": {"name": "string"}},
    "guild_set_leader": {"required": ["kind", "name"],
                         "params": {"name": "string"}},
    "guild_disband": {"required": ["kind"], "params": {}},
    "guild_rank": {"required": ["kind"],
                   "params": {"rank_id": "integer", "rights": "integer"}},
    "guild_add_rank": {"required": ["kind", "name"],
                      "params": {"name": "string"}},
    "guild_delete_rank": {"required": ["kind", "rank_id"],
                          "params": {"rank_id": "integer"}},

    # -- charter --
    "charter_list": {"required": ["kind"], "params": {}},
    "charter_buy": {"required": ["kind", "guid"],
                    "params": {"guid": "guid", "charter_type": "integer"}},
    "charter_signatures": {"required": ["kind"], "params": {"guid": "guid"}},
    "charter_query": {"required": ["kind"], "params": {"guid": "guid"}},
    "charter_rename": {"required": ["kind"],
                       "params": {"name": "string"}},
    "charter_offer": {"required": ["kind", "guid"],
                      "params": {"guid": "guid"}},
    "charter_sign": {"required": ["kind", "guid"],
                     "params": {"guid": "guid"}},
    "charter_decline": {"required": ["kind", "guid"],
                        "params": {"guid": "guid"}},
    "charter_turn_in": {"required": ["kind", "guid"],
                        "params": {"guid": "guid"}},

    # -- talents / skills --
    "talent_learn": {"required": ["kind", "tab", "talent"],
                     "params": {"tab": "integer", "talent": "integer"}},
    "talent_reset": {"required": ["kind"], "params": {}},
    "skill_unlearn": {"required": ["kind", "skill_id"],
                      "params": {"skill_id": "integer"}},

    # -- instance --
    "instance_reset": {"required": ["kind"], "params": {}},
    "instance_info": {"required": ["kind"], "params": {}},

    # -- meta (not game actions; handled by gateway) --
    "characters": {"required": ["kind"], "params": {}},
    "create": {"required": ["kind"], "params": {}},
    "delete_character": {"required": ["kind"], "params": {}},
    "select": {"required": ["kind"], "params": {}},
    "observe": {"required": ["kind"], "params": {}},
}

# Actions explicitly unavailable to agents (schema-enforced per RE doc)
AGENT_BLOCKED = {
    # chat / mail-writing disabled at gateway schema level
    "chat_say", "chat_yell", "chat_whisper", "chat_party", "chat_guild",
    "chat_raid", "mail_send",
    # GM / dev tools
    "player_tools", "gm_command", "teleport", "level_up", "add_item_gm",
    "custom_mount",
}


def validate_action(action):
    """
    Validate an action dict against the catalog.
    Returns (ok, error_message). Mirrors CowCraft's strict validation:
    unknown kind -> invalid; unknown field -> invalid (additionalProperties: false).
    """
    if not isinstance(action, dict):
        return False, "action must be an object"
    kind = action.get("kind")
    if kind not in ACTION_SCHEMAS:
        return False, f"unknown action kind: {kind!r}"
    if kind in AGENT_BLOCKED:
        return False, f"action not available to agents: {kind}"
    schema = ACTION_SCHEMAS[kind]
    allowed = set(schema["required"]) | set(schema["params"]) | {"kind"}
    for key in action:
        if key not in allowed:
            return False, f"unknown field {key!r} for action {kind}"
    for req in schema["required"]:
        if req == "kind":
            continue
        if req not in action:
            return False, f"missing required field {req!r} for action {kind}"
    return True, None


def action_names():
    """Authenticated, native-confirmed action names for list_actions."""
    return [k for k in CAPABILITIES if k not in AGENT_BLOCKED]
