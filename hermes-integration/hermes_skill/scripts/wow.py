#!/usr/bin/env python3
"""
wow.py — CLI helper for the cowcraft-wow Hermes skill.

Wraps CowCraftEnv with single-shot commands so a Hermes agent can drive a
WoW character from shell one-liners. Credentials via env vars.

Usage:
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py observe --character NAME
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py move --character NAME --dir north
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py attack --character NAME
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py loot --character NAME
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py interact --character NAME
    MINTA_USERNAME=... MINTA_PASSWORD=... python3 wow.py stop --character NAME

Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "systemone-integration"))

from cowcraft_env import CowCraftEnv, format_self_stats  # noqa: E402


def make_env(character, args=None):
    args = args or argparse.Namespace()
    return CowCraftEnv(
        gateway_url=os.environ.get("MINTA_GATEWAY_URL", "http://127.0.0.1:8901/mcp"),
        username=os.environ["MINTA_USERNAME"],
        password=os.environ["MINTA_PASSWORD"],
        character_name=character,
        model_name=os.environ.get("MINTA_AGENT_LABEL", "hermes-skill"),
        token_path=os.environ.get("MINTA_TOKEN_PATH"),
        model_path=os.environ.get("MINTA_MODEL_PATH"),
        char_class=getattr(args, "char_class", None) or int(os.environ.get("MINTA_CLASS", "1")),
        race=getattr(args, "race", None) if getattr(args, "race", None) is not None else int(os.environ.get("MINTA_RACE", "1")),
        gender=getattr(args, "gender", 0),
        skin=getattr(args, "skin", 0),
        face=getattr(args, "face", 0),
        hair_style=getattr(args, "hair_style", 0),
        hair_color=getattr(args, "hair_color", 0),
        facial_hair=getattr(args, "facial_hair", 0),
    )


def cmd_account(args):
    """Register this caller's account when it is new, then enter the character."""
    env = make_env(args.character, args)
    env.connect()
    print("account %s" % env.username)
    print("character %s" % env.character_name)
    env.close()


def cmd_character(args):
    """Create the named level-1 character when the account has none to enter."""
    env = make_env(args.character, args)
    env.connect()
    print("account %s" % env.username)
    print("character %s" % env.character_name)
    env.close()


def cmd_observe(args):
    env = make_env(args.character, args)
    env.connect()
    obs = env.observe()
    print(format_self_stats(obs.info))
    print(obs.text)


def cmd_stats(args):
    env = make_env(args.character, args)
    env.connect()
    obs = env.observe()
    print(format_self_stats(obs.info))


def cmd_move(args):
    env = make_env(args.character, args)
    env.connect()
    action = {"north": "move_north", "south": "move_south",
              "east": "move_east", "west": "move_west"}[args.dir]
    steps = max(1, min(args.steps, 10))
    for _ in range(steps):
        result = env.act(action)
        if not result.progressed:
            break
    obs = env.observe()
    print(obs.text.splitlines()[0] if obs.text else "(no observation)")


def cmd_simple(args, action):
    env = make_env(args.character, args)
    env.connect()
    result = env.act(action)
    obs = env.observe()
    print(f"action={action} progressed={result.progressed}")
    if obs.text:
        print(obs.text.splitlines()[0])


def main():
    ap = argparse.ArgumentParser(description="Drive a WoW character via the Minta gateway")
    ap.add_argument("--character", required=True, help="character name")
    ap.add_argument("--race", type=int, default=None, help="1 Human or 2 Orc")
    ap.add_argument("--class", dest="char_class", type=int, default=None, help="class id")
    ap.add_argument("--gender", type=int, default=0)
    ap.add_argument("--skin", type=int, default=0)
    ap.add_argument("--face", type=int, default=0)
    ap.add_argument("--hair-style", dest="hair_style", type=int, default=0)
    ap.add_argument("--hair-color", dest="hair_color", type=int, default=0)
    ap.add_argument("--facial-hair", dest="facial_hair", type=int, default=0)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("account", help="register this account if it is new, then enter")
    sub.add_parser("character", help="create the named character when none can be entered")
    sub.add_parser("observe", help="print current observation")
    sub.add_parser("stats", help="print own gold, hp, power, and position")

    p_move = sub.add_parser("move", help="move cardinally")
    p_move.add_argument("--dir", required=True, choices=["north", "south", "east", "west"])
    p_move.add_argument("--steps", type=int, default=1)

    sub.add_parser("attack", help="attack nearest hostile")
    sub.add_parser("loot", help="loot nearest corpse")
    sub.add_parser("interact", help="interact with nearest NPC/object")
    sub.add_parser("stop", help="stop all movement/combat")
    sub.add_parser("wait", help="do nothing this tick")

    args = ap.parse_args()
    if args.cmd == "account":
        cmd_account(args)
    elif args.cmd == "character":
        cmd_character(args)
    elif args.cmd == "observe":
        cmd_observe(args)
    elif args.cmd == "stats":
        cmd_stats(args)
    elif args.cmd == "move":
        cmd_move(args)
    else:
        cmd_simple(args, args.cmd)


if __name__ == "__main__":
    main()
