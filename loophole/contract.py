"""Goal Contract — the explicit, falsifiable definition of "done".

loophole refuses to run a goal that cannot be checked. The Goal Contract makes
the acceptance criteria, verifier boundary, and mutation policy explicit so that
"complete" means "the candidate satisfies THIS contract under a trusted verifier
boundary" — not "an LLM felt the goal was achieved".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, List, Optional


class VerifierKind(str, Enum):
    HARD = "hard"      # command exit-code; GRANTS completion
    SOFT = "soft"      # llm rubric; can only VETO when a hard verifier exists
    HUMAN = "human"    # checkpoint; pauses and asks


@dataclass
class Verifier:
    """A single acceptance check.

    hard:  ``command`` runs in a fresh checkout of the merged candidate; exit 0 = pass.
    soft:  ``rubric`` is given to an LLM that may only veto a hard pass.
    human: ``prompt`` is surfaced to the operator at a checkpoint.
    """
    kind: VerifierKind
    command: Optional[str] = None         # for hard
    rubric: Optional[str] = None          # for soft
    prompt: Optional[str] = None          # for human
    # Verification Boundary (fix B from the council):
    trusted_inputs: List[str] = field(default_factory=list)   # paths owned by orchestrator
    protected_paths: List[str] = field(default_factory=list)  # editing these => instant fail
    expected_test_delta: Optional[int] = None  # test count may not silently drop below baseline+delta
    environment: dict = field(default_factory=dict)
    allow_network: bool = False   # S1: opt this verifier out of the sandbox network deny

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            self.kind = VerifierKind(self.kind)
        if self.kind == VerifierKind.HARD and not self.command:
            raise ContractError("hard verifier requires a `command`")
        if self.kind == VerifierKind.SOFT and not self.rubric:
            raise ContractError("soft verifier requires a `rubric`")
        if self.kind == VerifierKind.HUMAN and not self.prompt:
            self.prompt = "Does the result satisfy the goal? (approve/reject)"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Verifier":
        d = dict(d)
        d["kind"] = VerifierKind(d.get("kind", "hard"))
        return cls(**d)


class ContractError(ValueError):
    """Raised when a Goal Contract is invalid (e.g. has no way to define done)."""


@dataclass
class GoalContract:
    """The full, falsifiable specification of a goal."""
    goal: str                                   # natural-language goal
    verifiers: List[Verifier] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    # Mutation policy:
    allowed_writes: List[str] = field(default_factory=lambda: ["**"])  # glob allowlist
    protected_paths: List[str] = field(default_factory=list)           # goal-wide protected
    # Non-functional requirements recorded for the residual-risk report:
    non_functional: List[str] = field(default_factory=list)
    # Budget ceilings:
    max_cost_usd: float = 0.0                    # 0 = unlimited (local models are free)
    max_tokens: int = 0                          # 0 = unlimited
    max_rounds: int = 25
    timeout_seconds: int = 7200

    def validate(self) -> None:
        """Reject any contract that cannot define 'done'."""
        if not self.goal or not self.goal.strip():
            raise ContractError("goal text is required")
        if not self.verifiers:
            raise ContractError(
                "a goal with no verifier is rejected: you cannot run-until-done "
                "without defining done. Add at least one --verify command, a soft "
                "rubric, or a human checkpoint."
            )
        # A soft verifier may only veto when a hard verifier exists; a contract
        # of soft-only is allowed ONLY when there are also human checkpoints,
        # otherwise nothing can ever grant completion.
        kinds = {v.kind for v in self.verifiers}
        can_grant = VerifierKind.HARD in kinds or VerifierKind.HUMAN in kinds
        if not can_grant:
            raise ContractError(
                "contract has only soft verifiers, which can VETO but never GRANT "
                "completion. Add a hard (command) verifier or a human checkpoint."
            )

    @property
    def hard_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.HARD]

    @property
    def soft_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.SOFT]

    @property
    def human_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.HUMAN]

    @property
    def all_protected_paths(self) -> List[str]:
        paths = list(self.protected_paths)
        for v in self.verifiers:
            paths.extend(v.protected_paths)
            paths.extend(v.trusted_inputs)
        return sorted(set(paths))

    def to_json(self) -> str:
        return json.dumps(
            {
                "goal": self.goal,
                "verifiers": [v.to_dict() for v in self.verifiers],
                "acceptance_criteria": self.acceptance_criteria,
                "allowed_writes": self.allowed_writes,
                "protected_paths": self.protected_paths,
                "non_functional": self.non_functional,
                "max_cost_usd": self.max_cost_usd,
                "max_tokens": self.max_tokens,
                "max_rounds": self.max_rounds,
                "timeout_seconds": self.timeout_seconds,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> "GoalContract":
        d = json.loads(raw)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> "GoalContract":
        verifiers = [Verifier.from_dict(v) for v in d.get("verifiers", [])]
        return cls(
            goal=d["goal"],
            verifiers=verifiers,
            acceptance_criteria=d.get("acceptance_criteria", []),
            allowed_writes=d.get("allowed_writes", ["**"]),
            protected_paths=d.get("protected_paths", []),
            non_functional=d.get("non_functional", []),
            max_cost_usd=d.get("max_cost_usd", 0.0),
            max_tokens=d.get("max_tokens", 0),
            max_rounds=d.get("max_rounds", 25),
            timeout_seconds=d.get("timeout_seconds", 7200),
        )

    @classmethod
    def quick(
        cls,
        goal: str,
        verify_cmd: Optional[str] = None,
        human: bool = False,
        **kwargs: Any,
    ) -> "GoalContract":
        """Build a minimal contract from CLI-style inputs."""
        verifiers: List[Verifier] = []
        if verify_cmd:
            verifiers.append(Verifier(kind=VerifierKind.HARD, command=verify_cmd))
        if human or not verify_cmd:
            verifiers.append(
                Verifier(kind=VerifierKind.HUMAN, prompt="Does the result satisfy: " + goal + "?")
            )
        c = cls(goal=goal, verifiers=verifiers, **kwargs)
        c.validate()
        return c
