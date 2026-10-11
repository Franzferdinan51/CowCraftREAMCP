#!/usr/bin/env python3
"""
swarm_coordinator.py — Enhanced multi-agent crew management for CowCraft.

Beyond systemone-integration/swarm.py, this adds:
- Role-based task assignment (tank engages, scout explores, support loots)
- Shared blackboard with pub/sub for inter-agent communication
- Leader election (highest-priority live agent; re-election on death)
- Formation movement (follow-the-leader with per-role offsets)
- Parallel decisions via auto_model_judge (up to 4 concurrent)

Config (swarm.json):
    {
        "gateway_url": "http://127.0.0.1:8901/mcp",
        "blackboard_file": "/tmp/swarm-blackboard.json",
        "tick_seconds": 6,
        "max_ticks": 0,
        "agents": [
            {"name": "Quacksmash", "role": "tank",
             "username": "DuckCrewTank", "password": "",
             "formation_offset": [0, 0]},
            {"name": "Featherstrike", "role": "scout",
             "username": "DuckCrewScout", "password": "",
             "formation_offset": [8, 8]},
            {"name": "Downshield", "role": "support",
             "username": "DuckCrewHealer", "password": "",
             "formation_offset": [-8, 4]}
        ]
    }
Passwords fall back to MINTA_PASSWORD_<NAME> env vars, then MINTA_PASSWORD.

Usage:
    python3 swarm_coordinator.py --config swarm.json

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "systemone-integration"))

from cowcraft_env import CowCraftEnv  # noqa: E402
from auto_model_judge import decide_parallel  # noqa: E402

ROLE_PRIORITY = {"tank": 0, "scout": 1, "support": 2}

# Role task hints injected into the system prompt.
ROLE_TASKS = {
    "tank": "You are the TANK. Engage the nearest hostile first. Hold position near the leader. Do not chase far.",
    "scout": "You are the SCOUT. Explore ahead of the formation (move toward unexplored directions). Report hostiles on the blackboard. Avoid engaging alone unless the target is weak.",
    "support": "You are SUPPORT. Stay near the leader (formation offset). Loot corpses after fights. Interact with NPCs/objects when the area is clear.",
}

_stop = threading.Event()


def _handle_signal(signum, frame):
    _stop.set()


class Blackboard:
    """File-backed shared state for the crew. Thread-safe, crash-safe."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self._data = {"messages": [], "leader": None, "positions": {},
                      "targets": {}, "updated": time.time()}
        self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                self._data.update(json.load(f))
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._data, f)
        os.replace(tmp, self.path)

    def post(self, agent, kind, text):
        with self._lock:
            self._data["messages"].append(
                {"t": time.time(), "agent": agent, "kind": kind, "text": text})
            self._data["messages"] = self._data["messages"][-100:]
            self._data["updated"] = time.time()
            self._save()

    def recent(self, kind=None, limit=10):
        with self._lock:
            msgs = self._data["messages"]
            if kind:
                msgs = [m for m in msgs if m["kind"] == kind]
            return list(msgs[-limit:])

    def set_leader(self, name):
        with self._lock:
            self._data["leader"] = name
            self._save()

    def get_leader(self):
        with self._lock:
            return self._data.get("leader")

    def set_position(self, agent, pos):
        with self._lock:
            self._data["positions"][agent] = {"pos": pos, "t": time.time()}
            self._save()

    def get_positions(self):
        with self._lock:
            return dict(self._data["positions"])


class CrewAgent(threading.Thread):
    def __init__(self, cfg, blackboard, gateway_url, tick_s):
        super().__init__(daemon=True, name=f"agent-{cfg['name']}")
        self.cfg = cfg
        self.bb = blackboard
        self.gateway_url = gateway_url
        self.tick_s = tick_s
        self.env = None
        self.alive_flag = True
        self.last_tick = 0
        self.memory = []

    def password(self):
        if self.cfg.get("password"):
            return self.cfg["password"]
        env_name = "MINTA_PASSWORD_" + self.cfg["name"].upper().replace("-", "_")
        return os.environ.get(env_name, os.environ.get("MINTA_PASSWORD", ""))

    def connect(self):
        delay = 2.0
        while not _stop.is_set():
            try:
                self.env = CowCraftEnv(
                    gateway_url=self.gateway_url,
                    username=self.cfg["username"],
                    password=self.password(),
                    character_name=self.cfg["name"],
                    char_class=self.cfg.get("char_class", 1),
                    role=self.cfg.get("role", "adventurer"),
                    model_name=f"swarm-{self.cfg['name']}",
                )
                self.env.connect()
                return True
            except Exception as e:
                self.bb.post(self.cfg["name"], "error", f"connect failed: {str(e)[:100]}")
                _stop.wait(delay)
                delay = min(delay * 1.5, 30.0)
        return False

    def build_state(self, tick):
        """Build the prompt state including role task, blackboard, formation."""
        role = self.cfg.get("role", "adventurer")
        task = ROLE_TASKS.get(role, "")
        leader = self.bb.get_leader()
        msgs = self.bb.recent(limit=5)
        bb_text = "\n".join(f"- {m['agent']}: {m['text']}" for m in msgs) or "(none)"
        leader_pos = self.bb.get_positions().get(leader, {}).get("pos") if leader else None
        formation = ""
        if leader and leader != self.cfg["name"] and leader_pos:
            ox, oy = self.cfg.get("formation_offset", [0, 0])
            formation = (f"\nLeader {leader} at {leader_pos}. "
                         f"Your formation offset is ({ox}, {oy}).")
        mem = "\n".join(self.memory[-4:]) if self.memory else "(none)"
        return (f"{task}\n\nBlackboard:\n{bb_text}{formation}\n\nMemory:\n{mem}")

    def run(self):
        if not self.connect():
            self.alive_flag = False
            return
        self.bb.post(self.cfg["name"], "status",
                     f"{self.cfg.get('role','?')} online")
        tick = 0
        while not _stop.is_set():
            try:
                obs = self.env.observe()
                state = self.build_state(tick) + f"\n\nTick {tick}:\n{obs.text}"
                # NOTE: single decide here; coordinator batches across agents
                # when using decide_parallel — see Coordinator.tick_all.
                action, conf, _model = self._decide(state)
                if conf < 0.35:
                    action = "wait"
                result = self.env.act(action)
                pos = getattr(self.env, "_pos", None)
                if pos:
                    self.bb.set_position(self.cfg["name"], list(pos))
                self.memory.append(
                    f"t{tick}: {action} ({conf:.2f}) "
                    f"{'ok' if result.progressed else '--'}")
                self.last_tick = tick
                tick += 1
            except Exception as e:
                self.bb.post(self.cfg["name"], "error", str(e)[:120])
                if not self.connect():
                    break
            _stop.wait(self.tick_s)
        self.alive_flag = False
        self.bb.post(self.cfg["name"], "status", "offline")

    def _decide(self, state):
        # Single-agent fallback; coordinator overrides with parallel batch.
        from auto_model_judge import decide
        action, conf, model, _advice = decide(state)
        return action, conf, model


class Coordinator:
    """Owns leader election, parallel ticks, and formation logic."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.bb = Blackboard(cfg.get("blackboard_file", "/tmp/swarm-blackboard.json"))
        self.gateway_url = cfg.get("gateway_url", "http://127.0.0.1:8901/mcp")
        self.tick_s = float(cfg.get("tick_seconds", 6))
        self.agents = [CrewAgent(a, self.bb, self.gateway_url, self.tick_s)
                       for a in cfg["agents"]]
        # Patch agents to use batched parallel decisions.
        for a in self.agents:
            a._decide = lambda state, _a=a: (None, None, None)  # placeholder

    def elect_leader(self):
        live = [a for a in self.agents if a.alive_flag and a.is_alive()]
        if not live:
            return None
        live.sort(key=lambda a: ROLE_PRIORITY.get(a.cfg.get("role"), 9))
        leader = live[0].cfg["name"]
        if self.bb.get_leader() != leader:
            self.bb.set_leader(leader)
            self.bb.post("coordinator", "leader", f"leader is now {leader}")
        return leader

    def run(self):
        for a in self.agents:
            a.start()
        # Wait for agents to come online
        time.sleep(5)
        tick = 0
        while not _stop.is_set():
            leader = self.elect_leader()
            live = [a for a in self.agents if a.alive_flag]
            if not live:
                print("[coordinator] no live agents — exiting", flush=True)
                break
            # Parallel decisions across live agents (up to 4).
            states = []
            order = []
            for a in live:
                try:
                    obs = a.env.observe() if a.env else None
                    if obs is None:
                        continue
                    state = a.build_state(tick) + f"\n\nTick {tick}:\n{obs.text}"
                    states.append((state, None, 20))
                    order.append((a, obs))
                except Exception:
                    continue
            if states:
                results = decide_parallel(states[:4])
                for (agent, obs), (action, conf, _m) in zip(order, results):
                    if conf < 0.35 or action not in (
                            "move_north", "move_south", "move_east", "move_west",
                            "attack", "stop", "interact", "loot", "wait"):
                        action = "wait"
                    try:
                        result = agent.env.act(action)
                        agent.memory.append(
                            f"t{tick}: {action} ({conf:.2f}) "
                            f"{'ok' if result.progressed else '--'}")
                        pos = getattr(agent.env, "_pos", None)
                        if pos:
                            self.bb.set_position(agent.cfg["name"], list(pos))
                        print(f"[t{tick:03d}] {agent.cfg['name']}: {action} "
                              f"conf={conf:.2f} {'ok' if result.progressed else '--'}",
                              flush=True)
                    except Exception as e:
                        self.bb.post(agent.cfg["name"], "error", str(e)[:120])
            # Support agents announce loot opportunities for the record.
            tick += 1
            _stop.wait(self.tick_s)
        _stop.set()
        for a in self.agents:
            a.join(timeout=5)


def main():
    ap = argparse.ArgumentParser(description="CowCraft swarm coordinator")
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    with open(args.config) as f:
        cfg = json.load(f)
    Coordinator(cfg).run()


if __name__ == "__main__":
    main()
