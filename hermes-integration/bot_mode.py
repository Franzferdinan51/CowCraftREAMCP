#!/usr/bin/env python3
"""
bot_mode.py — Run a CowCraft agent as a headless background bot.

No interactive input. Runs the See > Decide > Act loop autonomously until
told to stop. Designed to eventually plug into hermes-agent as a tool/skill.

Features:
- JSON config (character, role, model, gateway URL, tick interval)
- Graceful shutdown on SIGTERM / SIGINT
- Rotating file logs
- HTTP /health endpoint (for supervisors / hermes-agent polling)
- Auto-reconnect with backoff on gateway/CowCraft failures
- Never triggers new model loads (uses auto_model_judge)

Usage:
    python3 bot_mode.py --config bot.json
    python3 bot_mode.py --config bot.json --daemon   # (same; nohup yourself)

Config (bot.json):
    {
        "name": "Quacksmash",
        "role": "tank",
        "gateway_url": "http://127.0.0.1:8901/mcp",
        "username": "DuckCrewTank",
        "password": "secret",          # or use MINTA_PASSWORD env var
        "char_class": 1,
        "tick_seconds": 5,
        "max_ticks": 0,                # 0 = run forever
        "log_file": "/tmp/quacksmash.log",
        "health_port": 8891,
        "system_prompt": "optional override"
    }

Stdlib only.
"""

from __future__ import annotations

import argparse
import http.server
import json
import logging
import logging.handlers
import os
import signal
import socketserver
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "systemone-integration"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-gateway"))

from cowcraft_env import CowCraftEnv  # noqa: E402
from auto_model_judge import AutoModelJudge, bounded_world_summary  # noqa: E402
from cowcraft_mcp import account_state_paths  # noqa: E402
from recorder import TickLogger, training_log_path  # noqa: E402

VALID_ACTIONS = [
    "move_north", "move_south", "move_east", "move_west",
    "attack", "cast", "stop", "interact", "loot", "wait",
]

_stop = threading.Event()
_health = {"status": "starting", "tick": 0, "last_action": None,
           "last_error": None, "uptime_s": 0}


def _handle_signal(signum, frame):
    _stop.set()


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps(_health).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass  # quiet


def serve_health(port):
    try:
        with socketserver.TCPServer(("127.0.0.1", port), HealthHandler) as httpd:
            httpd.timeout = 1.0
            while not _stop.is_set():
                httpd.handle_request()
    except OSError as e:
        logging.getLogger("bot").warning("health endpoint unavailable: %s", e)


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    # password may come from env
    if not cfg.get("password"):
        cfg["password"] = os.environ.get("MINTA_PASSWORD", "")
    return cfg


def setup_logging(log_file, name):
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    if log_file:
        h = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=3)
    else:
        h = logging.StreamHandler()
    h.setFormatter(fmt)
    logger.addHandler(h)
    return logger


def connect_with_backoff(cfg, logger, make_env):
    """Connect to the gateway, retrying with backoff until told to stop."""
    delay = 2.0
    while not _stop.is_set():
        try:
            env = make_env()
            env.connect()
            logger.info("connected as %s (%s)", cfg["name"], cfg.get("role", "?"))
            return env
        except Exception as e:
            logger.warning("connect failed: %s — retry in %.0fs", str(e)[:120], delay)
            _health["last_error"] = str(e)[:200]
            _stop.wait(delay)
            delay = min(delay * 1.5, 60.0)
    return None


def run_bot(cfg):
    logger = setup_logging(cfg.get("log_file"), cfg["name"])
    logger.info("bot starting: %s role=%s", cfg["name"], cfg.get("role", "?"))
    _health["status"] = "running"
    started = time.time()

    # health endpoint
    if cfg.get("health_port"):
        threading.Thread(target=serve_health,
                         args=(cfg["health_port"],), daemon=True).start()

    judge = AutoModelJudge()
    training = None
    if cfg.get("record") is not False and os.environ.get("COWCRAFT_RECORD", "1") != "0":
        training = TickLogger(training_log_path(cfg.get("record_path")))
        logger.info("training rows append to a private file outside the repo")

    try:
        def make_env():
            root = os.environ.get("MINTA_COWCRAFT_STATE", "/tmp/cowcraft-accounts")
            token_path, model_path = account_state_paths(root, cfg["username"])
            return CowCraftEnv(
                gateway_url=cfg.get("gateway_url", "http://127.0.0.1:8901/mcp"),
                username=cfg["username"],
                password=cfg["password"],
                character_name=cfg["name"],
                char_class=cfg.get("char_class", 1),
                role=cfg.get("role", "adventurer"),
                model_name=cfg.get("model_name") or ("bot-" + cfg["name"]),
                token_path=token_path,
                model_path=model_path,
            )

        env = connect_with_backoff(cfg, logger, make_env)
        if env is None:
            logger.info("shutdown before connect")
            return 1

        tick = 0
        max_ticks = int(cfg.get("max_ticks", 0))
        tick_s = float(cfg.get("tick_seconds", 5))
        memory = []
        fail_streak = 0
        last_action = None

        while not _stop.is_set():
            if max_ticks and tick >= max_ticks:
                logger.info("max_ticks reached (%d)", max_ticks)
                break
            try:
                obs = env.observe()
                summary = bounded_world_summary(getattr(obs, "info", {}) or {})
                answers = judge(summary, [{"id": "action"}])
                main = answers.get("main") if isinstance(answers.get("main"), dict) else {}
                action = main.get("choice") or answers.get("action", {}).get("choice", "wait")
                conf = main.get("confidence", answers.get("action", {}).get("confidence", 0.0))
                advice = answers.get("advice") if isinstance(answers.get("advice"), dict) else {}
                if conf < 0.35 or action not in VALID_ACTIONS:
                    action = "wait"
                if action == last_action:
                    fail_streak += 1
                    if fail_streak >= 2:
                        action = "wait"
                        fail_streak = 0
                else:
                    fail_streak = 0
                result = env.act(action)
                if training is not None:
                    meta = answers.get("_meta") if isinstance(answers.get("_meta"), dict) else {}
                    training.log({
                        "type": "tick",
                        "observe": summary[:600],
                        "advice": advice.get("text"),
                        "advice_required": False,
                        "action": action,
                        "confidence": conf,
                        "model": meta.get("model"),
                        "progressed": bool(result.progressed),
                    })
                    training.next_tick()
                last_action = action
                memory.append(
                    f"tick {tick}: saw {obs.text.splitlines()[0][:70] if obs.text else '?'} "
                    f"| chose {action} ({conf:.2f}) | "
                    f"{'progressed' if result.progressed else 'no progress'}")
                logger.info("tick %d: %s conf=%.2f %s advice=%s", tick, action, conf,
                            "ok" if result.progressed else "--",
                            "none" if not advice.get("text") else "optional")
                _health.update(tick=tick, last_action=action, last_error=None,
                               uptime_s=int(time.time() - started))
                tick += 1
                if obs.done or result.done:
                    logger.info("env signalled done")
                    break
            except Exception as e:
                logger.warning("tick %d error: %s — reconnecting", tick, str(e)[:150])
                _health["last_error"] = str(e)[:200]
                try:
                    env = connect_with_backoff(cfg, logger, make_env)
                    if env is None:
                        break
                except Exception:
                    break
            _stop.wait(tick_s)

        _health["status"] = "stopped"
        logger.info("bot stopped after %d ticks", tick)
        return 0
    finally:
        if training is not None:
            training.close()


def main():
    ap = argparse.ArgumentParser(description="Headless CowCraft bot")
    ap.add_argument("--config", required=True, help="JSON config file")
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    cfg = load_config(args.config)
    sys.exit(run_bot(cfg))


if __name__ == "__main__":
    main()
