"""VIS-2 — render the run audit trail (the trust artifact for teams).

The store already keeps an append-only ``events`` log. For the acceptance-layer
bet, that log IS the product: it lets a team trust an unattended run without
reading every diff — what was planned, what each (untrusted) executor did, and
every BOUNDARY decision (merge-gate rejections, write-allowlist violations,
soft-judge escalations, crash reconciliations) with its reason. This module turns
the raw events into a readable trail; the local seed of the hosted control-plane.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional

# Events that represent the trusted boundary doing its job — surfaced loudly.
_GLYPH = {
    "goal_created": "•",
    "goal_status": "»",
    "task_status": "·",
    "task_done": "✓",
    "task_failed": "✗",
    "verify_run": "?",
    "merge_gate_reject": "✗",
    "merge_gate_reconcile": "⟲",
    "write_glob_violation": "!",
    "soft_fail_closed": "?",
    "verifier_bypasses": "!",
    "executor_network": "⇄",
    "executor_network_denied": "⛔",
    "auto_protect": "🛡",
}


def _payload(row: Any) -> dict:
    raw = row["payload"] if "payload" in row.keys() else None
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {"value": d}
    except (json.JSONDecodeError, TypeError):
        return {}


def _summary(kind: str, p: dict) -> str:
    if kind == "verify_run":
        s = "verify -> {} (score {})".format(
            "PASS" if p.get("passed") else "fail", int(p.get("score", 0)))
        if p.get("violations"):
            s += "  BOUNDARY VIOLATIONS: {}".format("; ".join(p["violations"])[:120])
        return s
    if kind == "merge_gate_reject":
        return "merge gate REJECTED: {}".format(
            "; ".join((p.get("failures") or []) + (p.get("violations") or []))[:160]
            or "verified red")
    if kind == "write_glob_violation":
        return "write-allowlist VIOLATION: {}".format("; ".join(p.get("violations", []))[:160])
    if kind == "soft_fail_closed":
        return "PAUSED for human (soft check indeterminate): {}".format(p.get("reason", ""))[:180]
    if kind == "merge_gate_reconcile":
        return "rolled HEAD back to last verified-green {}".format(str(p.get("reset_to", ""))[:12])
    if kind == "task_done":
        return "task done: {}".format(p.get("summary", ""))[:160]
    if kind == "task_failed":
        return "task failed: {}".format(p.get("error", ""))[:160]
    if kind == "goal_status":
        return "status -> {}".format(p.get("status", ""))
    if kind == "verifier_bypasses":
        return "adversary flagged verifier bypass(es)"
    # generic: compact the payload
    if p:
        return "; ".join("{}={}".format(k, str(v)[:60]) for k, v in list(p.items())[:3])
    return ""


def render_audit(goal_row: Any, events: List[Any], goal_text: str) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 68)
    add("loophole — Audit Trail")
    add("=" * 68)
    add("Goal:    {}".format(goal_text))
    add("Run:     {}  [{}]".format(goal_row["id"], goal_row["status"]))
    add("-" * 68)
    if not events:
        add("(no events recorded)")
        add("=" * 68)
        return "\n".join(lines)
    t0 = events[0]["ts"]
    for e in events:
        kind = e["kind"]
        glyph = _GLYPH.get(kind, "•")
        offset = "+{:>4}s".format(int(e["ts"] - t0))
        scope = ""
        if "task_id" in e.keys() and e["task_id"]:
            scope = " [{}]".format(e["task_id"])
        summary = _summary(kind, _payload(e))
        line = "{} {} {}{}".format(offset, glyph, kind, scope)
        if summary:
            line += ": " + summary
        add(line)
    add("-" * 68)
    add("This trail is the trust artifact: every boundary decision is recorded.")
    add("=" * 68)
    return "\n".join(lines)


def render_runs(goal_rows: List[Any], goal_text_of) -> str:
    """`goal_text_of(row) -> str` resolves the goal text from a row's contract."""
    lines: List[str] = []
    add = lines.append
    if not goal_rows:
        return "(no runs yet)"
    add("{:8} {:20} {}".format("STATUS", "RUN", "GOAL"))
    for g in goal_rows:
        add("{:8} {:20} {}".format(g["status"], g["id"][:20], goal_text_of(g)[:50]))
    return "\n".join(lines)
