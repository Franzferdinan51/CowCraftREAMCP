#!/usr/bin/env python3
"""
lmstudio_judge.py — Judge that talks directly to LM Studio (no SystemOne shim needed).

Uses an already-loaded decision model via the LM Studio OpenAI-compatible API.
The decision model is d1-3b. The fast fallback is d1-omni-600m.
Respects the user's constraint: never triggers new model loads.

    LMSTUDIO_URL=http://100.68.208.113:1234 python3 lmstudio_judge.py

Env vars:
    LMSTUDIO_URL — LM Studio base URL (default: http://100.68.208.113:1234)
    LMSTUDIO_MODEL — model ID (default: d1-3b)
    LMSTUDIO_API_KEY_FILE — path to API key file (default: ~/.config/lmstudio/api_key)
    HTTPS_PROXY — proxy for Tailscale access (auto-detected)
"""

import json
import os
import subprocess

LM_URL = os.environ.get("LMSTUDIO_URL", "http://100.68.208.113:1234").rstrip("/")
MODEL = os.environ.get("LMSTUDIO_MODEL", "d1-3b")
FAST_MODEL = "d1-omni-600m"
KEY_FILE = os.environ.get("LMSTUDIO_API_KEY_FILE", os.path.expanduser("~/.config/lmstudio/api_key"))

VALID_ACTIONS = [
    "move_north", "move_south", "move_east", "move_west",
    "attack", "stop", "interact", "loot", "wait",
]


def _api_key():
    try:
        return open(KEY_FILE).read().strip()
    except:
        return "lm-studio"


def _post_completion(payload):
    proxy = os.environ.get("HTTPS_PROXY", "").replace("http://", "").replace("https://", "")
    cmd = ["curl", "-s", f"{LM_URL}/v1/chat/completions",
           "-H", "Content-Type: application/json",
           "-H", f"Authorization: Bearer {_api_key()}",
           "-d", json.dumps(payload)]
    if proxy:
        cmd[2:2] = ["--proxy", proxy]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return json.loads(result.stdout)


def _read_action(data):
    raw = data["choices"][0]["message"]["content"].strip().lower()
    for action in VALID_ACTIONS:
        if action in raw:
            return action, 0.8
    return "wait", 0.3


def decide(state_text, system_prompt="You are a WoW agent. Reply with ONLY the action name.", max_tokens=20):
    """Ask the LM Studio model to pick an action. Returns (action, confidence)."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{state_text}\n\nPick one: {', '.join(VALID_ACTIONS)}"},
        ],
        "max_tokens": max_tokens,
    }
    try:
        return _read_action(_post_completion(payload))
    except Exception:
        if payload["model"] == FAST_MODEL:
            return "wait", 0.0
        payload["model"] = FAST_MODEL
        try:
            return _read_action(_post_completion(payload))
        except Exception:
            return "wait", 0.0


class LMStudioJudge:
    """Judge interface compatible with minta_agent.py: (state, questions) -> answers."""

    def __init__(self, model=None):
        self.model = model or MODEL

    def __call__(self, state_text, questions, images=(), videos=(), **kw):
        action, conf = decide(state_text)
        return {
            "action": {"choice": action, "confidence": conf},
            "_meta": {"model": self.model, "backend": "lmstudio"},
        }


def make_lmstudio_judge(model=None):
    return LMStudioJudge(model=model)
