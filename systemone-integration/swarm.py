#!/usr/bin/env python3
"""
swarm.py — run a multi-agent crew on the Minta gateway.

Each agent gets its own thread, its own gateway session (own CowCraft
account/character), its own role, and its own SystemOne model routing.
Agents share a blackboard (JSON file) for coordination: positions,
targets, and callouts.

    MINTA_PASSWORD=... python3 swarm.py --config swarm_config.json

Config schema (see swarm_config.example.json):
{
  "gateway_url": "http://127.0.0.1:8901/mcp",
  "blackboard": "/tmp/minta_blackboard.json",
  "ticks": 60,
  "agents": [
    {"name": "Junoduck", "username": "Juno", "role": "tank",
     "char_class": 1, "model": "d1-3b"},
    {"name": "Quackers", "username": "DuckCrew1", "role": "scout",
     "char_class": 1, "model": "d1-omni-600m"}
  ]
}

Passwords: set MINTA_PASSWORD_<USERNAME> per agent, or a single
MINTA_PASSWORD shared across the crew (accounts must still be distinct).
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cowcraft_env import CowCraftEnv, ACTIONS  # noqa: E402
from judge import make_judge, build_questions  # noqa: E402
from minta_agent import SYSTEM_PROMPT, run_builtin_loop  # noqa: E402


class Blackboard:
    """Tiny shared state for the crew (file-backed, thread-safe)."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        if not os.path.exists(path):
            self._write({})

    def _write(self, data):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    def update(self, name, patch):
        with self._lock:
            try:
                with open(self.path) as f:
                    data = json.load(f)
            except Exception:
                data = {}
            data.setdefault(name, {}).update(patch)
            data[name]["updated"] = time.time()
            self._write(data)

    def read(self):
        with self._lock:
            try:
                with open(self.path) as f:
                    return json.load(f)
            except Exception:
                return {}


def run_agent(cfg, gateway_url, blackboard, ticks, questions):
    name = cfg["name"]
    username = cfg.get("username", name)
    pw_env = f"MINTA_PASSWORD_{username.upper()}"
    password = os.environ.get(pw_env) or os.environ.get("MINTA_PASSWORD")
    if not password:
        print(f"[{name}] no password ({pw_env} / MINTA_PASSWORD); skipping", flush=True)
        return
    env = CowCraftEnv(
        gateway_url=gateway_url,
        username=username,
        password=password,
        character_name=name,
        char_class=cfg.get("char_class", 1),
        role=cfg.get("role", "adventurer"),
        model_name=f"minta-swarm-{name}",
    )
    try:
        env.connect()
    except Exception as exc:
        print(f"[{name}] connect failed: {exc}", flush=True)
        return
    judge = make_judge(model=cfg.get("model"))
    prompt = SYSTEM_PROMPT.format(name=name, role=cfg.get("role", "adventurer"))
    prompt += (
        "\nYou are part of a crew. Coordinate: don't attack what a crewmate "
        "already claimed, share scouting info, stay near the group."
    )

    memory = []
    for tick in range(ticks):
        try:
            obs = env.observe()
        except Exception as exc:
            print(f"[{name}] observe failed: {exc}; retrying", flush=True)
            time.sleep(2)
            continue
        blackboard.update(name, {"pos": list(env._pos), "hp": list(env._hp)})
        state = (
            f"{prompt}\n\nCrew blackboard:\n"
            + json.dumps(blackboard.read(), indent=None)[:800]
            + "\n\nMemory:\n"
            + ("\n".join(memory[-6:]) if memory else "(none)")
            + f"\n\nTick {tick}:\n{obs.text}"
        )
        answers = judge(state, questions)
        action = answers.get("action", {}).get("choice", "wait")
        conf = float(answers.get("action", {}).get("confidence", 0.0) or 0.0)
        if conf < 0.35:
            action = "wait"
        result = env.act(action)
        memory.append(f"{action} ({conf:.2f}) {'ok' if result.progressed else '--'}")
        print(f"[{name} {tick:03d}] {action} conf={conf:.2f} "
              f"{'ok' if result.progressed else '--'}", flush=True)
    env.close()
    print(f"[{name}] done", flush=True)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="swarm_config.json")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = json.load(f)
    gateway_url = cfg.get("gateway_url",
                          os.environ.get("MINTA_GATEWAY_URL", "http://127.0.0.1:8901/mcp"))
    blackboard = Blackboard(cfg.get("blackboard", "/tmp/minta_blackboard.json"))
    ticks = int(cfg.get("ticks", 60))
    questions = build_questions(ACTIONS)

    threads = []
    for agent in cfg.get("agents", []):
        t = threading.Thread(target=run_agent,
                             args=(agent, gateway_url, blackboard, ticks, questions),
                             name=f"agent-{agent['name']}", daemon=True)
        t.start()
        threads.append(t)
        time.sleep(2)  # stagger logins; gateway is serial per account
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
