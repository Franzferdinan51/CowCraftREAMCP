#!/usr/bin/env python3
"""
judge.py — SystemOne judges for the Minta agent loop.

Two judges, same interface: (state_text, questions, images, videos) -> answers
in SystemOne api shapes: {name: {"choice"/..., "confidence": f}, "_meta": {...}}.

1. SystemOneShimJudge — talks to a live SystemOne shim over HTTP
   (SYSTEMONE_SHIM_URL, default http://127.0.0.1:8765) using
   POST /v1/systemone/decide. This is the real "brain".

2. StubJudge — deterministic offline fallback: greedy toward the nearest
   hostile when healthy, retreats when hurt, wanders otherwise. Lets the
   loop run end-to-end with no models loaded.

make_judge() picks the shim when reachable, else the stub.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

SHIM_URL = os.environ.get("SYSTEMONE_SHIM_URL", "http://127.0.0.1:8765").rstrip("/")


def _post(path, payload, timeout=60):
    req = urllib.request.Request(
        SHIM_URL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class SystemOneShimJudge:
    """Judge backed by the SystemOne shim's /v1/systemone/decide endpoint."""

    def __init__(self, shim_url=None, model=None):
        self.shim_url = (shim_url or SHIM_URL).rstrip("/")
        self.model = model

    def __call__(self, state_text, questions, images=(), videos=(), **kw):
        # SystemOne decide takes one typed question; we send the action
        # choice plus gates as a batch-shaped single call.
        action_q = next(
            (q for q in questions if q.get("id") == "action" or "action" in q.get("id", "")),
            questions[0] if questions else None,
        )
        payload = {
            "state": state_text,
            "instructions": (
                "You are the brain of a World of Warcraft agent. "
                "Pick exactly one action from the listed options."
            ),
            "type": "choice",
            "criteria": action_q or {},
        }
        if self.model:
            payload["model"] = self.model
        try:
            out = _post_shim(self.shim_url, "/v1/systemone/decide", payload)
        except Exception as exc:
            raise RuntimeError(f"systemone shim unreachable: {exc}") from exc
        choice = out.get("choice") or out.get("label") or "wait"
        conf = float(out.get("confidence", 0.5))
        answers = {
            "action": {"choice": choice, "confidence": conf},
            "_meta": {"backend": out.get("backend", "systemone"), "model": self.model},
        }
        # carry through any extra typed answers the shim returned
        for q in questions:
            qid = q.get("id")
            if qid and qid != "action" and qid in out:
                answers[qid] = out[qid]
        return answers


def _post_shim(base, path, payload, timeout=60):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class StubJudge:
    """Offline heuristic judge: fight when healthy, retreat when hurt."""

    def __call__(self, state_text, questions, images=(), videos=(), **kw):
        text = state_text.lower()
        # crude hp parse from the env's render format "hp=NN/MM"
        import re
        m = re.search(r"hp=(\d+)/(\d+)", text)
        frac = 1.0
        if m:
            frac = int(m.group(1)) / max(1, int(m.group(2)))
        hostile = "hostile=true" in text and "dead=false" in text
        if frac < 0.35:
            choice = "move_south"  # retreat
        elif hostile and frac > 0.6:
            choice = "attack"
        elif "nearby objects" in text:
            choice = "interact"
        else:
            choice = "move_north"
        return {
            "action": {"choice": choice, "confidence": 0.55},
            "_meta": {"backend": "stub"},
        }


def make_judge(model=None):
    """Prefer the live SystemOne shim; fall back to the stub judge."""
    judge = SystemOneShimJudge(model=model)
    try:
        _post_shim(judge.shim_url, "/v1/systemone/decide", {
            "state": "ping", "instructions": "ping", "type": "noul",
        })
        return judge
    except Exception:
        return StubJudge()


def build_questions(actions):
    """Question set for the loop: action choice + stuck/progress gates."""
    return [
        {"id": "action", "type": "choice",
         "question": "Which action should the agent take next?",
         "options": [{"name": a} for a in actions]},
        {"id": "stuck", "type": "noul",
         "question": "Is the agent stuck (repeating positions with no progress)?",
         "criteria": {"yes": "repeating same area, no progress",
                      "no": "moving or making progress"}},
        {"id": "progress", "type": "score",
         "question": "Rate overall progress toward the agent's goal.",
         "levels": ["0", "1", "2", "3", "4"]},
    ]
