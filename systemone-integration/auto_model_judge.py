#!/usr/bin/env python3
"""
auto_model_judge.py — Auto-detects what's loaded in LM Studio and uses it.

Never triggers a new model load. Queries /v1/models and calls only
d1-3b, then d1-omni-600m when that id is already loaded. Other models
are not called.

Supports up to 4 parallel requests via thread pool (for swarm use).

This avoids RAM overload by never loading new models.

Env vars:
    LMSTUDIO_URL — LM Studio base URL (default: http://100.68.208.113:1234)
    LMSTUDIO_API_KEY_FILE — path to API key (default: ~/.config/lmstudio/api_key)
    PREFER_MODEL — ignored. Decisions use d1-3b, then d1-omni-600m.
    MAX_PARALLEL — max concurrent model requests (default: 4)
"""

import json
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor

LM_URL = os.environ.get("LMSTUDIO_URL", "http://100.68.208.113:1234").rstrip("/")
KEY_FILE = os.environ.get("LMSTUDIO_API_KEY_FILE", os.path.expanduser("~/.config/lmstudio/api_key"))
# Same pair as SystemOne's DEFAULT_DECISION_MODEL and DEFAULT_FAST_MODEL.
DECISION_MODEL = "d1-3b"
FAST_MODEL = "d1-omni-600m"
DECISION_MODELS = (DECISION_MODEL, FAST_MODEL)
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", "4"))

VALID_ACTIONS = [
    "move_north", "move_south", "move_east", "move_west",
    "attack", "cast", "stop", "interact", "loot", "wait",
]


def choose_action(loaded_models, reply, valid_actions=None, load_log=None):
    """Pick one already-loaded action, or wait.

    An empty model list or a reply that is not a known action yields wait.
    This never loads a model and never raises.
    """
    log = load_log if isinstance(load_log, list) else []
    try:
        actions = list(VALID_ACTIONS if valid_actions is None else valid_actions)
    except TypeError:
        return "wait", log
    if not loaded_models:
        return "wait", log
    text = "" if reply is None else str(reply).strip().lower()
    words = re.findall(r"[a-z0-9_]+", text)
    for action in actions:
        if not isinstance(action, str):
            continue
        name = action.strip().lower()
        if name and (text == name or name in words):
            return name, log
    return "wait", log


def bounded_world_summary(state, limit=600):
    """Short self / hostile / money text. Quests and objects are left out."""
    state = state if isinstance(state, dict) else {}
    me = state.get("self") if isinstance(state.get("self"), dict) else {}
    name = me.get("name", "")
    hp = me.get("hp", me.get("health", ""))
    money = me.get("money", state.get("money", ""))
    parts = ["self name=%s hp=%s money=%s" % (name, hp, money)]
    hostiles = []
    units = state.get("nearby_units") or []
    if isinstance(units, list):
        for unit in units:
            if not isinstance(unit, dict) or unit.get("dead"):
                continue
            if unit.get("hostile") is True or unit.get("faction") == 32:
                hostiles.append(str(unit.get("name") or "hostile"))
    if hostiles:
        parts.append("hostiles " + ", ".join(hostiles))
    text = "; ".join(parts)
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = 600
    if cap < 1:
        cap = 1
    if len(text) > cap:
        text = text[:cap]
    return text

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


def select_decision_model(loaded_models):
    """Return d1-3b if that id is listed, else d1-omni-600m.

    Any other loaded model is ignored. An empty list returns None.
    """
    found = []
    for item in loaded_models or []:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
    for wanted in DECISION_MODELS:
        for mid in found:
            if wanted in mid.lower():
                return mid
    return None


def other_decision_model(chosen, loaded_models):
    """The other D1 id, when it is loaded and different from chosen."""
    chosen_l = (chosen or "").lower()
    for wanted in DECISION_MODELS:
        if wanted in chosen_l:
            continue
        alt = select_decision_model(
            [mid for mid in (loaded_models or [])
             if isinstance(mid, str) and wanted in mid.lower()])
        if alt and alt.lower() != chosen_l:
            return alt
    return None


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
    """Pick d1-3b, else d1-omni-600m, from models already loaded.

    Does not load a model. Returns None when neither id is loaded.
    """
    global _cached_model
    with _model_lock:
        if _cached_model:
            return _cached_model
        chosen = select_decision_model(get_loaded_models())
        if chosen:
            _cached_model = chosen
        return chosen


def _ask_model(model, state_text, system_prompt, max_tokens):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{state_text}\n\nPick one: {', '.join(VALID_ACTIONS)}"},
        ],
        "max_tokens": max_tokens,
    }
    out = _curl([
        f"{LM_URL}/v1/chat/completions",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {_api_key()}",
        "-d", json.dumps(payload),
    ], timeout=60)
    data = json.loads(out)
    raw = data["choices"][0]["message"]["content"].strip().lower()
    action, _log = choose_action([model], raw)
    words = re.findall(r"[a-z0-9_]+", raw)
    if action in words or raw.strip() == action:
        return action, 0.8, model
    return "wait", 0.3, model


def decide(state_text, system_prompt=None, max_tokens=20):
    """Ask d1-3b, then d1-omni-600m, to pick an action.

    Returns (action, confidence, model_used). Neither call happens unless
    that model id is already loaded. A failed call tries the other id once.
    """
    loaded = get_loaded_models()
    model = select_decision_model(loaded)
    if not model:
        return "wait", 0.0, None

    if system_prompt is None:
        system_prompt = "You are a WoW agent. Reply with ONLY the action name."

    try:
        return _ask_model(model, state_text, system_prompt, max_tokens)
    except Exception:
        alt = other_decision_model(model, loaded)
        if not alt:
            return "wait", 0.0, model
        try:
            return _ask_model(alt, state_text, system_prompt, max_tokens)
        except Exception:
            return "wait", 0.0, alt


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
