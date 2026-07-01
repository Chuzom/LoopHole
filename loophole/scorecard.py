"""Value scorecard — what LoopHole actually did for you, quantified.

LoopHole's pitch is "you can trust the result." This turns that into numbers the
user sees after every run (and in aggregate via ``loophole stats``): how many cheats
the boundary blocked, how many merges the verifier rejected before accepting, whether
'done' is verifier-backed. It's the measurable-value + feedback layer — pure read
over the event log, no engine changes.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

_BOUNDARY_KINDS = {"write_glob_violation", "merge_gate_reject",
                   "soft_fail_closed", "boundary_violation"}


def _payload(e: Any) -> dict:
    raw = e["payload"] if "payload" in e.keys() else None
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def run_scorecard(store: Any, goal_id: str) -> Dict[str, Any]:
    """A JSON-able scorecard for one run."""
    g = store.get_goal(goal_id)
    if not g:
        return {}
    events = list(store.events(goal_id))
    tasks = list(store.tasks_for_goal(goal_id))
    verify_runs = [e for e in events if e["kind"] == "verify_run"]
    passes = [e for e in verify_runs if _payload(e).get("passed")]
    boundary = [e for e in events if e["kind"] in _BOUNDARY_KINDS]
    status = g["status"]
    return {
        "goal_id": goal_id,
        "status": status,
        "verified_done": status == "done",
        "rounds": len(verify_runs),
        "verifier_rejections": len(verify_runs) - len(passes),  # bad candidates refused
        "cheats_blocked": len(boundary),                        # boundary saves
        "tasks_merged": sum(1 for t in tasks if t.status == "done"),
        "tasks_failed": sum(1 for t in tasks if t.status == "failed"),
        "duration_s": max(0.0, (g["updated_at"] or 0) - (g["created_at"] or 0)),
    }


def _dur(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return "{}s".format(s)
    if s < 3600:
        return "{}m{}s".format(s // 60, s % 60)
    return "{}h{}m".format(s // 3600, (s % 3600) // 60)


def render_scorecard(sc: Dict[str, Any], color: bool = True) -> str:
    if not sc:
        return ""
    g, y, r, dim, rst = ("\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m") if color else ("",) * 5
    head = (g + "✓ VERIFIED DONE" + rst) if sc["verified_done"] else (r + "✗ NOT DONE" + rst)
    lines = [
        "── LoopHole scorecard ─────────────────────────",
        "  {}   ({} rounds · {})".format(head, sc["rounds"], _dur(sc["duration_s"])),
        "  {} agent merge(s) accepted by the verifier".format(sc["tasks_merged"]),
        "  {}{} candidate(s) the verifier REJECTED before accepting{}".format(
            y, sc["verifier_rejections"], rst),
        "  {}{} cheat(s) the boundary blocked{}  {}".format(
            g if sc["cheats_blocked"] else dim, sc["cheats_blocked"], rst,
            dim + "(writes outside allowlist, weakened tests, etc.)" + rst),
        dim + "  trust = the verifier said done, not an agent." + rst,
    ]
    return "\n".join(lines)


def aggregate(store: Any) -> Dict[str, Any]:
    """Cross-run scorecard for ``loophole stats`` — the 'lots of feedback' view."""
    cards = [run_scorecard(store, g["id"]) for g in store.list_goals()]
    cards = [c for c in cards if c]
    done = [c for c in cards if c["verified_done"]]
    return {
        "runs": len(cards),
        "verified_done": len(done),
        "failed_or_open": len(cards) - len(done),
        "total_cheats_blocked": sum(c["cheats_blocked"] for c in cards),
        "total_rejections": sum(c["verifier_rejections"] for c in cards),
        "total_merges": sum(c["tasks_merged"] for c in cards),
    }


def render_aggregate(agg: Dict[str, Any], color: bool = True) -> str:
    g, dim, rst = ("\033[32m", "\033[2m", "\033[0m") if color else ("", "", "")
    if not agg.get("runs"):
        return "No runs yet. Try `loophole demo` or `loophole run \"<goal>\"`."
    return "\n".join([
        "── LoopHole · all runs ────────────────────────",
        "  {} run(s):  {}{} verified-done{}  ·  {} failed/open".format(
            agg["runs"], g, agg["verified_done"], rst, agg["failed_or_open"]),
        "  {} agent merge(s) accepted by the verifier".format(agg["total_merges"]),
        "  {} candidate(s) rejected before acceptance".format(agg["total_rejections"]),
        "  {}{} cheat(s) blocked across all runs{}".format(g, agg["total_cheats_blocked"], rst),
        dim + "  every 'done' above was verifier-backed — that's the guarantee." + rst,
        dim + "  details: loophole runs  ·  loophole audit <goal-id>" + rst,
    ])
