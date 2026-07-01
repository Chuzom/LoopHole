"""Routing-quality feedback: loophole verdicts as ground truth for Chuzom.

Chuzom routes each task to the cheapest capable model, but "capable" is a
guess until something checks the output. loophole's verifier IS that check:
if the cheap executor's work passed a falsifiable verifier, the routing was
good; if the run failed/paused, it wasn't. After every run we emit one record
so Chuzom can learn which models actually hold up per task — grounded, not
self-reported.

Delivery is best-effort and NEVER fails the run: POST to a live Chuzom router
(``CHUZOM_URL`` + ``/feedback``) when reachable, else append to
``~/.chuzom/quality_feedback.jsonl`` for the Chuzom side to ingest later.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional


def _provider_label(p: Any) -> str:
    """A stable 'provider:model' label for a role's provider (unwraps proxies)."""
    inner = getattr(p, "_inner", p)      # ChargingProvider delegates, but be explicit
    name = getattr(inner, "name", None) or "unknown"
    model = getattr(inner, "model", "") or ""
    if not model:
        return name
    if model.startswith(name + ":"):     # e.g. chuzom model is already 'chuzom:moderate'
        return model
    return "{}:{}".format(name, model)


def build_record(store: Any, goal_id: str, status: str, rounds: int,
                 budget: Any, planner_label: str, executor_label: str,
                 ts: float) -> dict:
    """Assemble the routing-quality record from the run's outcome."""
    from .scorecard import run_scorecard
    sc = run_scorecard(store, goal_id) or {}
    return {
        "source": "loophole",
        "goal_id": goal_id,
        "planner_model": planner_label,
        "executor_model": executor_label,
        "status": status,                       # done | paused | failed
        "verified_done": status == "done",      # the ground-truth signal
        "rounds": rounds,
        "verifier_rejections": sc.get("verifier_rejections", 0),
        "cheats_blocked": sc.get("cheats_blocked", 0),
        "tokens": getattr(budget, "spent_tokens", 0),
        "cost_usd": getattr(budget, "spent_usd", 0.0),
        "ts": ts,
    }


def _feedback_path() -> str:
    d = os.path.join(os.path.expanduser("~"), ".chuzom")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "quality_feedback.jsonl")


def emit(record: dict, chuzom_url: Optional[str] = None) -> str:
    """Deliver the record. Returns the sink used ('http'|'file'|'none').

    Best-effort: any failure falls back to the JSONL file, and a file failure
    is swallowed — feedback must never break a run.
    """
    url = chuzom_url if chuzom_url is not None else os.environ.get("CHUZOM_URL")
    if url:
        try:
            body = json.dumps(record).encode()
            req = urllib.request.Request(
                url.rstrip("/") + "/feedback", data=body,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=3) as r:
                if 200 <= getattr(r, "status", 200) < 300:
                    return "http"
        except (urllib.error.URLError, OSError, ValueError):
            pass                                  # fall through to the file
    try:
        with open(_feedback_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return "file"
    except OSError:
        return "none"
