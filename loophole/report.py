"""Residual-Risk Report (council critique #8, fix F).

loophole does not claim to prove a goal. It proves a candidate satisfies the
declared contract under a trusted verifier boundary — and states plainly what it
could NOT prove. This module renders that honest report.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .contract import GoalContract
from .verifier import VerifyVerdict


def _not_proven(contract: GoalContract, status: str) -> List[str]:
    """The 'What was NOT proven' bullets — shared by the text report and the
    JSON output so the two never drift apart."""
    out: List[str] = []
    if contract.soft_verifiers:
        out.append("Soft/subjective criteria are advisory only (LLM veto, not proof).")
    if contract.human_verifiers and status != "done":
        out.append("Human checkpoints were not all approved.")
    for nf in contract.non_functional:
        out.append("Non-functional requirement not independently proven: {}".format(nf))
    if not contract.non_functional and not contract.soft_verifiers:
        out.append("Only the declared hard verifier(s) were checked. Anything outside "
                   "their scope (security, performance, real-world correctness) is unproven.")
    return out


def _next_steps(status: str, verdict: Optional[VerifyVerdict]) -> List[str]:
    """Concrete, status-specific guidance for what the operator does next."""
    if status == "done":
        return []
    steps: List[str] = []
    if verdict and verdict.needs_human:
        steps.append("A soft check could not be evaluated automatically — review the "
                     "candidate and decide, then re-run.")
    if status == "paused":
        steps.append("Paused for input or because it stopped making progress. "
                     "Inspect, then: loophole resume <goal-id>.")
    elif status == "failed":
        steps.append("No passing candidate was produced. Read the FAIL reasons above, "
                     "adjust the goal/verifier, then: loophole resume <goal-id>.")
    return steps


def residual_risk_report(contract: GoalContract, verdict: Optional[VerifyVerdict],
                         status: str, rounds_used: int, budget_summary: str,
                         verifier_bypasses: Optional[List[str]] = None,
                         detail: str = "",
                         guarded_actions: Optional[List[dict]] = None) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 64)
    add("loophole — Residual-Risk Report")
    add("=" * 64)
    add("Goal: {}".format(contract.goal))
    add("Outcome: {}".format(status.upper()))
    if status != "done" and detail:
        add("Reason: {}".format(detail))
    add("Rounds used: {}".format(rounds_used))
    add("Budget: {}".format(budget_summary))
    add("")

    add("What was VERIFIED:")
    if verdict and verdict.results:
        for r in verdict.results:
            mark = "ABSTAIN" if getattr(r, "abstained", False) else \
                   ("PASS" if r.passed else "FAIL")
            extra = ""
            if r.metrics:
                extra = "  ({})".format(", ".join(
                    "{}={}".format(k, int(v)) for k, v in r.metrics.items() if v))
            add("  [{}] {}{}".format(mark, r.name, extra))
            # surface WHY a check failed/abstained, not just that it did
            if mark != "PASS" and r.failures:
                add("        -> {}".format("; ".join(r.failures[:3])))
    else:
        add("  (no verifier results)")
    add("")

    if verdict and verdict.boundary_violations:
        add("Verification BOUNDARY VIOLATIONS (verifier may have been tampered with):")
        for v in verdict.boundary_violations:
            add("  ! {}".format(v))
        add("")

    if verdict and verdict.needs_human:
        add("Needs HUMAN sign-off (could not be auto-evaluated):")
        for n in verdict.needs_human:
            add("  ? {}".format(n))
        add("")

    # Module SDK: graded (non-code) verdicts carry a confidence + residual risk.
    if verdict and getattr(verdict, "confidence", 1.0) < 1.0:
        add("GRADED VERDICT (module verifiers — not a deterministic proof):")
        add("  confidence: {:.0%}".format(verdict.confidence))
        for r in (verdict.residual_risk or []):
            add("  ~ residual: {}".format(r))
        add("")

    add("What was NOT proven (residual risk):")
    for np in _not_proven(contract, status):
        add("  - {}".format(np))
    add("")

    if verifier_bypasses:
        add("Known verifier bypasses flagged before the run (adversary review):")
        for b in verifier_bypasses:
            add("  - {}".format(b))
        add("")

    if guarded_actions:
        tier = getattr(contract, "privilege_tier", "guarded")
        n = len(guarded_actions)
        if tier == "full":
            # full-privilege mission: don't nag, but never hide it — point to the trail.
            add("Guarded actions: {} side-effecting action(s) ran under the 'full' "
                "privilege tier (see `loophole audit`).".format(n))
            add("")
        else:
            add("Guarded actions taken (audit) — side-effecting/irreversible commands "
                "the swarm ran (allowed under the '{}' tier, recorded here):".format(tier))
            for ga in guarded_actions:
                add("  - [{}] {}".format(ga.get("category", "?"), ga.get("command", "")))
            add("")

    steps = _next_steps(status, verdict)
    if steps:
        add("NEXT STEPS:")
        for s in steps:
            add("  > {}".format(s))
        add("")

    add("Completion claim: \"the candidate satisfies the declared Goal Contract")
    add("under the trusted verifier boundary\" — NOT \"the goal is provably achieved\".")
    add("=" * 64)
    return "\n".join(lines)


# Schema at loophole/schemas/run_result.schema.json — bump on any breaking change
# (removed/renamed field, changed type). Additive fields don't need a bump.
SCHEMA_VERSION = 1

# Boundary-decision event kinds surfaced in the JSON output (mirrors audit.py's
# glyph map minus the routine/progress kinds — PR comments want the trust-relevant
# events, not every task_status tick).
_BOUNDARY_EVENT_KINDS = {
    "merge_gate_reject", "write_glob_violation", "soft_fail_closed",
    "merge_gate_reconcile", "verifier_bypasses", "executor_network_denied",
    "auto_protect", "guarded_action", "human_fail_closed",
}


def collect_guarded_actions(store: Any, goal_id: str) -> List[dict]:
    """The guarded side-effecting actions recorded during a run (for the report)."""
    out: List[dict] = []
    for e in store.events(goal_id):
        if e["kind"] != "guarded_action":
            continue
        raw = e["payload"] if "payload" in e.keys() else None
        try:
            p = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            p = {}
        out.append({"command": p.get("command", ""), "category": p.get("category", "?")})
    return out


def to_json(contract: GoalContract, outcome: Any, store: Any, goal_id: str) -> Dict[str, Any]:
    """Machine-readable run result — the CI/PR-comment/Action contract.

    Validates against ``loophole/schemas/run_result.schema.json``. Reuses the
    same data the text report and scorecard already compute, so the JSON and
    the human-readable output can never disagree about what happened.
    """
    from .audit import _payload, _summary   # local: avoid a module-load cycle
    from .scorecard import run_scorecard

    verdict = outcome.verdict
    sc = run_scorecard(store, goal_id) or {}

    verified = []
    if verdict and verdict.results:
        for r in verdict.results:
            verified.append({
                "name": r.name,
                "passed": bool(r.passed),
                "abstained": bool(getattr(r, "abstained", False)),
                "metrics": dict(r.metrics or {}),
                "failures": list(r.failures or []),
            })

    boundary_events = []
    for e in store.events(goal_id):
        if e["kind"] not in _BOUNDARY_EVENT_KINDS:
            continue
        p = _payload(e)
        boundary_events.append({
            "kind": e["kind"],
            "ts": e["ts"] if "ts" in e.keys() else 0.0,
            "task_id": e["task_id"] if "task_id" in e.keys() else None,
            "detail": _summary(e["kind"], p),
            "payload": p,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "goal_id": goal_id,
        "goal": contract.goal,
        "status": outcome.status,
        "exit_code": 0 if outcome.status == "done" else 1,
        "verified_done": outcome.status == "done",
        "detail": outcome.detail or "",
        "rounds": outcome.rounds,
        "verified": verified,
        "not_proven": _not_proven(contract, outcome.status),
        "boundary_events": boundary_events,
        "cheats_blocked": sc.get("cheats_blocked", 0),
        "verifier_rejections": sc.get("verifier_rejections", 0),
        "verifier_bypasses": list(outcome.verifier_bypasses or []),
        "budget": {
            "usd": outcome.budget.spent_usd,
            "tokens": outcome.budget.spent_tokens,
        },
        "duration_s": sc.get("duration_s", 0.0),
        "residual_risk_text": residual_risk_report(
            contract, verdict, outcome.status, outcome.rounds,
            outcome.budget.summary(), outcome.verifier_bypasses, detail=outcome.detail),
    }
