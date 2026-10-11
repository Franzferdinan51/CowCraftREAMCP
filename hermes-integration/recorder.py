#!/usr/bin/env python3
"""
recorder.py — Record and replay CowCraft agent sessions.

Record mode wraps a CowCraftEnv: every observe/decide/act tick is appended
as one JSON line. Replay mode steps through a recording for debugging or
as training data.

Usage:
    # Record (wraps any env-compatible loop; simplest: use with bot_mode)
    python3 recorder.py record --out session.jsonl -- python3 bot_mode.py --config bot.json
    # ...but easiest is programmatic: see RecordedEnv below.

    # Replay: print each tick
    python3 recorder.py replay --in session.jsonl

    # Replay: verify a judge reproduces the recorded actions
    python3 recorder.py verify --in session.jsonl

    # Stats
    python3 recorder.py stats --in session.jsonl

Format (one JSON object per line):
    {"tick": 0, "t": 1699999999.0, "type": "observe", "text": "..."}
    {"tick": 0, "t": 1699999999.5, "type": "decide", "action": "attack",
     "confidence": 0.8, "model": "d1-3b"}
    {"tick": 0, "t": 1699999999.6, "type": "act", "action": "attack",
     "progressed": true}

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "systemone-integration"))


class TickLogger:
    """Append-only JSONL tick log."""

    def __init__(self, path):
        self.path = path
        self.tick = 0
        self._f = open(path, "a")

    def log(self, entry):
        entry = dict(entry)
        entry.setdefault("tick", self.tick)
        entry.setdefault("t", time.time())
        self._f.write(json.dumps(entry) + "\n")
        self._f.flush()

    def next_tick(self):
        self.tick += 1

    def close(self):
        self._f.close()


class RecordedEnv:
    """Wraps a CowCraftEnv, logging every observe/act. Pair with a judge
    wrapper to log decisions too (see record_decision)."""

    def __init__(self, env, logger):
        self._env = env
        self._log = logger

    def __getattr__(self, name):
        return getattr(self._env, name)

    def observe(self):
        obs = self._env.observe()
        self._log.log({"type": "observe", "text": obs.text[:2000],
                       "done": obs.done})
        return obs

    def act(self, action):
        result = self._env.act(action)
        self._log.log({"type": "act", "action": action,
                       "progressed": result.progressed, "done": result.done})
        return result


def record_decision(logger, action, confidence, model=None):
    logger.log({"type": "decide", "action": action,
                "confidence": confidence, "model": model})


def cmd_replay(args):
    with open(args.in_file) as f:
        for line in f:
            e = json.loads(line)
            t = e.get("type")
            if t == "observe":
                print(f"[t{e['tick']}] OBSERVE: {e['text'][:160]}...")
            elif t == "decide":
                print(f"[t{e['tick']}] DECIDE: {e['action']} "
                      f"conf={e.get('confidence', 0):.2f} model={e.get('model')}")
            elif t == "act":
                print(f"[t{e['tick']}] ACT: {e['action']} "
                      f"{'ok' if e.get('progressed') else '--'}")
            if args.step:
                input("  <enter> for next tick...")


def cmd_stats(args):
    ticks, actions, progressed = set(), {}, 0
    models = set()
    with open(args.in_file) as f:
        for line in f:
            e = json.loads(line)
            ticks.add(e.get("tick"))
            if e.get("type") == "decide":
                actions[e.get("action")] = actions.get(e.get("action"), 0) + 1
                if e.get("model"):
                    models.add(e["model"])
            if e.get("type") == "act" and e.get("progressed"):
                progressed += 1
    print(f"ticks: {len(ticks)}")
    print(f"actions: {json.dumps(actions, indent=2)}")
    print(f"progressed acts: {progressed}")
    print(f"models: {sorted(models) or '(none recorded)'}")


def cmd_verify(args):
    """Check a judge reproduces recorded actions (needs LM Studio)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from auto_model_judge import decide
    total = match = 0
    last_obs = None
    with open(args.in_file) as f:
        for line in f:
            e = json.loads(line)
            if e.get("type") == "observe":
                last_obs = e["text"]
            elif e.get("type") == "decide" and last_obs:
                action, conf, _m, _advice = decide(last_obs[:1500])
                total += 1
                if action == e.get("action"):
                    match += 1
                last_obs = None
    print(f"reproduced {match}/{total} decisions ({100.0*match/total:.0f}%)" if total else "no decisions found")


def main():
    ap = argparse.ArgumentParser(description="Record/replay CowCraft sessions")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("replay", help="step through a recording")
    p.add_argument("--in", dest="in_file", required=True)
    p.add_argument("--step", action="store_true", help="pause between ticks")

    p = sub.add_parser("stats", help="summarize a recording")
    p.add_argument("--in", dest="in_file", required=True)

    p = sub.add_parser("verify", help="check judge reproduces recorded actions")
    p.add_argument("--in", dest="in_file", required=True)

    args = ap.parse_args()
    {"replay": cmd_replay, "stats": cmd_stats, "verify": cmd_verify}[args.cmd](args)


if __name__ == "__main__":
    main()
