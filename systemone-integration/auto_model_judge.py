#!/usr/bin/env python3
"""
auto_model_judge.py — Auto-detects what's loaded in LM Studio and uses it.

Never triggers a new model load. d1-3b and d1-omni-600m advise the
main model what to do and what to use in game. That advice is not
required. The main model, any other loaded model, chooses the action.

Supports up to 4 parallel requests via thread pool (for swarm use).

This avoids RAM overload by never loading new models.

Env vars:
    LMSTUDIO_URL — LM Studio base URL (default: http://100.68.208.113:1234)
    LMSTUDIO_API_KEY_FILE — path to API key (default: ~/.config/lmstudio/api_key)
    PREFER_MODEL — ignored. d1-3b and d1-omni-600m only give advice.
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
    "attack", "cast", "stop", "interact", "loot", "stats", "wait",
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
    """Short self stats, gold, and hostiles. Quests and objects are left out."""
    from cowcraft_env import format_self_stats
    state = state if isinstance(state, dict) else {}
    parts = [format_self_stats(state)]
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


def _loaded_ids(loaded_models):
    found = []
    for item in loaded_models or []:
        if isinstance(item, str) and item.strip():
            found.append(item.strip())
    return found


def decision_model_ids(loaded_models):
    """Loaded d1-3b and d1-omni-600m. These advise. They are not the action."""
    found = _loaded_ids(loaded_models)
    chosen = []
    for wanted in DECISION_MODELS:
        for mid in found:
            if wanted in mid.lower() and mid not in chosen:
                chosen.append(mid)
                break
    return chosen


def main_model_id(loaded_models):
    """The loaded model that plays. A decision model is not the main model."""
    for mid in _loaded_ids(loaded_models):
        if not any(wanted in mid.lower() for wanted in DECISION_MODELS):
            return mid
    return None


def advice_text(parts):
    """Join decision-model replies into advice the main model may ignore."""
    lines = []
    for mid, raw in parts or []:
        text = " ".join(str(raw or "").split())
        if text:
            lines.append("%s: %s" % (mid, text[:180]))
    if not lines:
        return None
    return "Decision advice, not required. " + " | ".join(lines)


def prompt_with_advice(state_text, advice):
    if not advice:
        return state_text
    return (
        "%s\n\n%s\nThe main model is not required to follow this."
        % (state_text, advice)
    )


def executed_action(main_reply, advice=None):
    """The main model's reply is the action. Advice does not change it."""
    if main_reply is None or not str(main_reply).strip():
        return "wait"
    action, _log = choose_action(["main"], main_reply)
    return action


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


ADVICE_PROMPT = (
    "You advise the main model about this character. "
    "Say what it could do and what it could use in game. "
    "The main model is not required to follow you."
)
MAIN_PROMPT = (
    "You are the main model playing this character. "
    "Decision-model advice may be included and is not required. "
    "Reply with ONLY the action name."
)


def pick_model():
    """The loaded main model. Does not load one and does not return a decision model."""
    global _cached_model
    with _model_lock:
        if _cached_model:
            return _cached_model
        chosen = main_model_id(get_loaded_models())
        if chosen:
            _cached_model = chosen
        return chosen


def _complete(model, state_text, system_prompt, max_tokens):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state_text},
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
    return data["choices"][0]["message"]["content"].strip()


def collect_advice(loaded_models, state_text, ask):
    """Ask each loaded decision model. A failed call is skipped."""
    parts = []
    for mid in decision_model_ids(loaded_models):
        try:
            raw = ask(mid, state_text, ADVICE_PROMPT, 40)
        except Exception:
            continue
        parts.append((mid, raw))
    return advice_text(parts)


def decide(state_text, system_prompt=None, max_tokens=20):
    """Main model chooses the action. Decision models only attach advice.

    Returns (action, confidence, model_used, advice). advice is None when
    no decision model answered. Their text is not executed.
    """
    loaded = get_loaded_models()
    advice = collect_advice(
        loaded, state_text,
        lambda model, text, prompt, limit: _complete(model, text, prompt, limit),
    )
    main = main_model_id(loaded)
    if not main:
        return "wait", 0.0, None, advice
    prompt = system_prompt or MAIN_PROMPT
    try:
        raw = _complete(main, prompt_with_advice(state_text, advice), prompt, max_tokens)
    except Exception:
        return "wait", 0.0, main, advice
    action = executed_action(raw, advice)
    if action == "wait":
        return action, 0.3, main, advice
    return action, 0.8, main, advice


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
    return [f.result()[:3] for f in futures]


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
        action, conf, model, advice = decide(state_text)
        return {
            "main": {"choice": action, "confidence": conf},
            "action": {"choice": action, "confidence": conf},
            "advice": {"text": advice, "required": False},
            "_meta": {
                "model": model,
                "backend": "lmstudio-auto",
                "no_new_loads": True,
                "advice_required": False,
            },
        }

    def decide_batch(self, states):
        """Decide for multiple states in parallel. Returns list of answers."""
        requests = [(s, None, 20) for s in states]
        results = decide_parallel(requests)
        return [
            {
                "main": {"choice": action, "confidence": conf},
                "action": {"choice": action, "confidence": conf},
                "advice": {"text": None, "required": False},
                "_meta": {
                    "model": model,
                    "backend": "lmstudio-auto",
                    "parallel": True,
                    "advice_required": False,
                },
            }
            for action, conf, model in results
        ]


def make_auto_judge():
    """Create judge using auto-detected loaded model."""
    return AutoModelJudge()
