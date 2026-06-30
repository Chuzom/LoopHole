"""Residual-Risk Report (council critique #8, fix F).

loophole does not claim to prove a goal. It proves a candidate satisfies the
declared contract under a trusted verifier boundary — and states plainly what it
could NOT prove. This module renders that honest report.
"""

from __future__ import annotations

from typing import List, Optional

from .contract import GoalContract
from .verifier import VerifyVerdict


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
                         detail: str = "") -> str:
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

    add("What was NOT proven (residual risk):")
    if contract.soft_verifiers:
        add("  - Soft/subjective criteria are advisory only (LLM veto, not proof).")
    if contract.human_verifiers and status != "done":
        add("  - Human checkpoints were not all approved.")
    for nf in contract.non_functional:
        add("  - Non-functional requirement not independently proven: {}".format(nf))
    if not contract.non_functional and not contract.soft_verifiers:
        add("  - Only the declared hard verifier(s) were checked. Anything outside")
        add("    their scope (security, performance, real-world correctness) is unproven.")
    add("")

    if verifier_bypasses:
        add("Known verifier bypasses flagged before the run (adversary review):")
        for b in verifier_bypasses:
            add("  - {}".format(b))
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
