"""Plan critic + verifier adversary (council critique #8, fix F).

Two pre-execution checks that spend a little to avoid wasting a lot:

1. plan_critic: a strong-model adversarial review of the DAG against the goal.
   "Does this plan actually solve the goal, or the wrong problem?"
2. verifier_adversary: attacks the verifier. "How could an agent pass these
   checks WITHOUT satisfying intent?" Obvious bypass -> pause before spending.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List

from .contract import GoalContract
from .provider import Provider, Msg
from .scheduler import PlannedTask


@dataclass
class CritiqueResult:
    approved: bool
    issues: List[str] = field(default_factory=list)
    raw: str = ""


_CRITIC_SYSTEM = """You review a proposed task DAG against a goal. Be skeptical.
Identify: missing tasks, tasks that solve the WRONG problem, wrong dependencies,
and anything that would make the goal unmet even if every task succeeds.
Return ONLY JSON: {"approved": true|false, "issues": ["..."]}.
Approve only if the plan, fully executed, would plausibly satisfy the goal."""

_ADVERSARY_SYSTEM = """You are a red-teamer attacking an acceptance verifier.
Given a goal and its verifiers, list concrete ways an agent could make the
verifiers PASS without actually satisfying the goal's intent (reward hacking:
weakening tests, stubbing, special-casing inputs, editing config, etc.).
Return ONLY JSON: {"bypasses": ["..."]}. Empty list means the verifier looks robust."""


def _json(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return {}
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return {}


def critique_plan(provider: Provider, contract: GoalContract,
                  tasks: List[PlannedTask]) -> CritiqueResult:
    plan_repr = [
        {"id": t.id, "description": t.description, "depends_on": t.depends_on,
         "writes": t.writes} for t in tasks
    ]
    user = "GOAL:\n{}\n\nPROPOSED PLAN:\n{}".format(
        contract.goal, json.dumps(plan_repr, indent=2))
    comp = provider.complete([Msg("system", _CRITIC_SYSTEM), Msg("user", user)],
                             temperature=0.2)
    d = _json(comp.text)
    approved = d.get("approved")
    if not isinstance(approved, bool):
        # Fail-closed: an unparseable / missing verdict does NOT approve the plan.
        # A malformed critic response triggers a replan, not a free pass (mirrors
        # the soft-judge S11 fix). The degenerate-plan circuit breaker bounds any
        # loop if the critic stays broken.
        return CritiqueResult(
            approved=False,
            issues=["plan critic verdict unparseable; rejecting to be safe"],
            raw=comp.text,
        )
    return CritiqueResult(
        approved=approved,
        issues=[str(x) for x in d.get("issues", [])],
        raw=comp.text,
    )


def attack_verifier(provider: Provider, contract: GoalContract) -> List[str]:
    vs = [{"command": v.command, "kind": v.kind.value,
           "protected_paths": v.protected_paths} for v in contract.verifiers]
    user = "GOAL:\n{}\n\nVERIFIERS:\n{}".format(contract.goal, json.dumps(vs, indent=2))
    comp = provider.complete([Msg("system", _ADVERSARY_SYSTEM), Msg("user", user)],
                             temperature=0.4)
    d = _json(comp.text)
    return [str(x) for x in d.get("bypasses", [])]
