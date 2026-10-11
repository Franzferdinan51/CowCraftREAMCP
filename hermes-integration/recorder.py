#!/usr/bin/env python3
"""
recorder.py — Record and replay CowCraft agent sessions.

bot_mode appends one private JSON row per tick under ~/.cowcraft/training/.
That directory is outside the git checkout. Passwords and connection tokens
are dropped before a row is written. Recording is off unless "record" is true
or COWCRAFT_RECORD=1. Replay and stats read that file locally.

Usage:
    python3 recorder.py replay --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl
    python3 recorder.py stats --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl
    python3 recorder.py verify --in ~/.cowcraft/training/cowcraft-YYYYMMDD.jsonl

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
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "systemone-integration"))


_SECRET_KEYS = {"password", "token", "connection", "authorization", "api_key"}
_TOKEN = re.compile(r"[a-f0-9-]{36}\.[a-f0-9]{64}")


def repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def outside_repo(path):
    """True when path is not inside the CowCraft git checkout."""
    root = repo_root()
    target = os.path.abspath(path)
    return target != root and not target.startswith(root + os.sep)


def local_training_path():
    """Private JSONL under the home directory. Never a path in the repo."""
    root = os.path.expanduser("~/.cowcraft/training")
    os.makedirs(root, mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    return os.path.join(root, "cowcraft-%s.jsonl" % time.strftime("%Y%m%d"))


def sanitize_entry(entry):
    """Drop secrets and connection tokens before a training row is written."""
    if isinstance(entry, dict):
        out = {}
        for key, value in entry.items():
            if str(key).lower() in _SECRET_KEYS:
                continue
            out[key] = sanitize_entry(value)
        return out
    if isinstance(entry, list):
        return [sanitize_entry(value) for value in entry]
    if isinstance(entry, str):
        return _TOKEN.sub("[redacted]", entry)
    return entry


def recording_enabled(cfg=None, env=None):
    """Training rows stay off unless config or COWCRAFT_RECORD turns them on."""
    cfg = cfg or {}
    if env is None:
        env = os.environ
    if cfg.get("record") is False:
        return False
    if cfg.get("record") is True:
        return True
    return env.get("COWCRAFT_RECORD") == "1"


def training_log_path(requested=None):
    """Use a private path. An in-repo request is redirected home."""
    if requested and outside_repo(requested):
        parent = os.path.dirname(os.path.abspath(requested))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        return os.path.abspath(requested)
    return local_training_path()


class TickLogger:
    """Append-only JSONL tick log. The file is mode 0600."""

    def __init__(self, path):
        self.path = path
        self.tick = 0
        self._f = open(path, "a")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def log(self, entry):
        entry = sanitize_entry(entry)
        entry.setdefault("tick", self.tick)
        entry.setdefault("t", time.time())
        self._f.write(json.dumps(entry) + "\n")
        self._f.flush()

    def next_tick(self):
        self.tick += 1

    def close(self):
        handle = self._f
        self._f = None
        if handle is not None and not handle.closed:
            handle.close()


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
            elif t == "tick":
                print(f"[t{e['tick']}] TICK: {e.get('action')} "
                      f"conf={float(e.get('confidence') or 0):.2f} "
                      f"{'ok' if e.get('progressed') else '--'} "
                      f"advice={'optional' if e.get('advice') else 'none'}")
            if args.step:
                input("  <enter> for next tick...")


def cmd_stats(args):
    ticks, actions, progressed = set(), {}, 0
    models = set()
    with open(args.in_file) as f:
        for line in f:
            e = json.loads(line)
            ticks.add(e.get("tick"))
            if e.get("type") in ("decide", "tick"):
                actions[e.get("action")] = actions.get(e.get("action"), 0) + 1
                if e.get("model"):
                    models.add(e["model"])
            if e.get("type") in ("act", "tick") and e.get("progressed"):
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
