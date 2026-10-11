#!/usr/bin/env python3
"""
minta_agent.py — run one SystemOne-driven agent on the Minta gateway.

    MINTA_USERNAME=... MINTA_PASSWORD=... \
        python3 minta_agent.py --name Junoduck --role scout --ticks 40

Pipeline per tick: SEE (gateway observe) -> DECIDE (SystemOne judge) ->
ACT (gateway action) -> loop compresses the turn into memory.

Uses SystemOne's DecisionLoop when the `systemone` package is importable,
otherwise a minimal built-in loop with the same See > Decide > Act shape.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cowcraft_env import CowCraftEnv, ACTIONS, ActResult  # noqa: E402
from judge import make_judge, build_questions  # noqa: E402

SYSTEM_PROMPT = (
    "You are {name}, a {role} in vanilla World of Warcraft, controlled through "
    "an MCP gateway. Each tick you see the world as text and pick ONE action. "
    "Prefer fighting hostiles near your level, looting corpses, and interacting "
    "with objects. Retreat (move away) when below 35% health. Never repeat a "
    "failing action more than twice."
)


def run_builtin_loop(env, judge, questions, ticks, system_prompt):
    """Minimal See > Decide > Act loop (fallback when systemone isn't installed)."""
    memory = []
    for tick in range(ticks):
        obs = env.observe()
        state = (
            f"{system_prompt}\n\nMemory:\n"
            + ("\n".join(memory[-6:]) if memory else "(none)")
            + f"\n\nTick {tick}:\n{obs.text}"
        )
        answers = judge(state, questions)
        action = answers.get("action", {}).get("choice", "wait")
        conf = answers.get("action", {}).get("confidence", 0.0)
        if conf < 0.35:
            action = "wait"  # uncertainty gate
        result = env.act(action)
        memory.append(
            f"I saw: {obs.text.splitlines()[0][:80]} | "
            f"I chose: {action} ({conf:.2f}) | "
            f"{'progressed' if result.progressed else 'no progress'}"
        )
        print(f"[{tick:03d}] {action} conf={conf:.2f} "
              f"{'ok' if result.progressed else '--'}", flush=True)
        if obs.done or result.done:
            break
    return memory


def run_systemone_loop(env, judge, questions, ticks, system_prompt):
    from systemone.loop import DecisionLoop

    loop = DecisionLoop(
        judge=judge,
        action_name="action",
        uncertainty_action="wait",
        confidence_floor=0.35,
        escalate_below=0.70,
        budget=ticks,
        system_prompt=system_prompt,
    )
    result = loop.run(env, questions)
    for s in result.steps:
        print(f"[{s.tick:03d}] {s.action} conf={s.confidence:.2f} "
              f"{'ok' if s.progressed else '--'}", flush=True)
    print(f"outcome={result.outcome} ticks={result.n_ticks}", flush=True)
    return result.memory


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=os.environ.get("MINTA_CHARACTER", "Junoduck"))
    ap.add_argument("--role", default="scout")
    ap.add_argument("--char-class", type=int, default=1)
    ap.add_argument("--gateway", default=os.environ.get(
        "MINTA_GATEWAY_URL", "http://127.0.0.1:8901/mcp"))
    ap.add_argument("--model", default=os.environ.get("MINTA_MODEL"),
                    help="SystemOne model override (shim routes otherwise)")
    ap.add_argument("--ticks", type=int, default=40)
    args = ap.parse_args()

    env = CowCraftEnv(
        gateway_url=args.gateway,
        character_name=args.name,
        char_class=args.char_class,
        role=args.role,
    )
    print(f"connecting {args.name} ({args.role}) -> {args.gateway} ...", flush=True)
    env.connect()
    print("connected.", flush=True)

    judge = make_judge(model=args.model)
    backend = getattr(judge, "_meta", None)
    print(f"judge: {type(judge).__name__}", flush=True)
    questions = build_questions(ACTIONS)
    system_prompt = SYSTEM_PROMPT.format(name=args.name, role=args.role)

    try:
        try:
            import systemone  # noqa: F401
            run_systemone_loop(env, judge, questions, args.ticks, system_prompt)
        except ImportError:
            print("systemone package not installed; using built-in loop", flush=True)
            run_builtin_loop(env, judge, questions, args.ticks, system_prompt)
    except KeyboardInterrupt:
        print("interrupted", flush=True)
    finally:
        env.close()


if __name__ == "__main__":
    main()
