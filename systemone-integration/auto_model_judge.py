#!/usr/bin/env python3
"""
auto_model_judge.py — Auto-detects what's loaded in LM Studio and uses it.

Never triggers a new model load. Queries /v1/models to see what's available,
prefers D1 for fast decisions, falls back to whatever's loaded.

Supports up to 4 parallel requests via thread pool (for swarm use).

This avoids RAM overload by never loading new models.

Env vars:
    LMSTUDIO_URL — LM Studio base URL (default: http://100.68.208.113:1234)
    LMSTUDIO_API_KEY_FILE — path to API key (default: ~/.config/lmstudio/api_key)
    PREFER_MODEL — preferred model ID substring (default: "d1")
    MAX_PARALLEL — max concurrent model requests (default: 4)
"""

import json
import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

LM_URL = os.environ.get("LMSTUDIO_URL", "http://100.68.208.113:1234").rstrip("/")
KEY_FILE = os.environ.get("LMSTUDIO_API_KEY_FILE", os.path.expanduser("~/.config/lmstudio/api_key"))
PREFER = os.environ.get("PREFER_MODEL", "d1").lower()
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "4"))

VALID_ACTIONS = [
    "move_north", "move_south", "move_east", "move_west",
    "attack", "stop", "interact", "loot", "wait",
]

_cached_model = None
_model_lock = threading.Lock()

# Shared thread pool for parallel model requests (max 4)
_executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL, thread_name_prefix="judge")


def _api_key():
    try:
        return open(KEY_FILE).read().strip()
    except:
        return "lm-studio"


def _curl(args, timeout=15):
    """Run curl with proxy support for Tailscale."""
    proxy = os.environ.get("HTTPS_PROXY", "").replace("http://", "").replace("https://", "")
    # Also check lowercase
    if not proxy:
        proxy = os.environ.get("https_proxy", "").replace("http://", "").replace("https://", "")
    cmd = ["curl", "-s"] + args
    if proxy:
        # curl needs just host:port, strip auth
        if "@" in proxy:
            proxy = proxy.split("@")[-1]
        cmd[2:2] = ["--proxy", f"http://{proxy}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return result.stdout


def get_loaded_models():
    """Query LM Studio for currently loaded models. Never triggers a load."""
    try:
        out = _curl([
            f"{LM_URL}/v1/models",
            "-H", f"Authorization: Bearer {_api_key()}",
        ])
        data = json.loads(out)
        return [m["id"] for m in data.get("data", [])]
    except Exception:
        return []


def pick_model():
    """Pick the best available model without loading anything new.

    Priority:
    1. Model matching PREFER (default: d1) — fast decisions
    2. Any other loaded model
    3. None (caller should use StubJudge fallback)
    """
    global _cached_model
    with _model_lock:
        if _cached_model:
            return _cached_model

        models = get_loaded_models()
        if not models:
            return None

        # Prefer D1 or matching model
        for m in models:
            if PREFER in m.lower():
                _cached_model = m
                return m

        # Fall back to first available
        _cached_model = models[0]
        return models[0]


def decide(state_text, system_prompt=None, max_tokens=20):
    """Ask the auto-detected model to pick an action. Returns (action, confidence, model_used)."""
    model = pick_model()
    if not model:
        return "wait", 0.0, None

    if system_prompt is None:
        system_prompt = "You are a WoW agent. Reply with ONLY the action name."

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{state_text}\n\nPick one: {', '.join(VALID_ACTIONS)}"},
        ],
        "max_tokens": max_tokens,
    }
    try:
        out = _curl([
            f"{LM_URL}/v1/chat/completions",
            "-H", "Content-Type: application/json",
            "-H", f"Authorization: Bearer {_api_key()}",
            "-d", json.dumps(payload),
        ], timeout=60)
        data = json.loads(out)
        raw = data["choices"][0]["message"]["content"].strip().lower()
        for action in VALID_ACTIONS:
            if action in raw:
                return action, 0.8, model
        return "wait", 0.3, model
    except Exception:
        return "wait", 0.0, model


def decide_parallel(requests):
    """Run multiple decide() calls in parallel (up to MAX_PARALLEL).

    Args:
        requests: list of (state_text, system_prompt, max_tokens) tuples

    Returns:
        list of (action, confidence, model) tuples in input order
    """
    futures = [
        _executor.submit(decide, state, prompt, tokens)
        for state, prompt, tokens in requests
    ]
    return [f.result() for f in futures]


class AutoModelJudge:
    """Judge that auto-uses whatever's loaded in LM Studio. Never loads new models.

    Thread-safe: multiple agents can call concurrently (up to MAX_PARALLEL).
    """

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    @property
    def model(self):
        with self._lock:
            if not self._model:
                self._model = pick_model()
            return self._model

    def __call__(self, state_text, questions, images=(), videos=(), **kw):
        action, conf, model = decide(state_text)
        return {
            "action": {"choice": action, "confidence": conf},
            "_meta": {"model": model, "backend": "lmstudio-auto", "no_new_loads": True},
        }

    def decide_batch(self, states):
        """Decide for multiple states in parallel. Returns list of answers."""
        requests = [(s, None, 20) for s in states]
        results = decide_parallel(requests)
        return [
            {
                "action": {"choice": action, "confidence": conf},
                "_meta": {"model": model, "backend": "lmstudio-auto", "parallel": True},
            }
            for action, conf, model in results
        ]


def make_auto_judge():
    """Create judge using auto-detected loaded model."""
    return AutoModelJudge()
