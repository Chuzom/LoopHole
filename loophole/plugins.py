"""Module SDK — the stable seam that lets separately-shipped modules (e.g. a
finance module) extend LoopHole without touching core.

A module registers verifier *kinds* and their evaluators. Core dispatches a
non-builtin verifier kind to the registered evaluator at the ROUND level only —
module verifiers never enter the deterministic per-merge gate or the verify cache,
so the strict "code" path is untouched (the firewall lives in
``GoalContract.validate`` + ``domain``).

A module is a normal Python package exposing ``register(api)``; it is discovered
via the ``loophole.modules`` entry-point group, or registered directly in-process.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class VerifierContext:
    """What a module evaluator gets: the candidate checkout dir plus hooks to log
    provenance and (optionally) call models. Everything a graded verdict needs to
    be inspectable lands in the event log via ``log``."""
    store: Any = None
    goal_id: Optional[str] = None
    base_commit: Optional[str] = None
    providers: Any = None   # Roles, for PANEL/judge verifiers

    def log(self, kind: str, payload: dict) -> None:
        if self.store is not None and self.goal_id is not None:
            try:
                self.store.log(kind, goal_id=self.goal_id, payload=payload)
            except Exception:
                pass


@dataclass
class ModuleResult:
    """A module verifier's GRADED result. Unlike a hard pass/fail, it carries a
    confidence and explicit residual risk — never a bare 'verified' stamp."""
    name: str
    passed: bool                       # vetoes round-level completion when False
    confidence: float = 1.0            # 1.0 = certain; <1.0 = probabilistic
    residual_risk: List[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    detail: str = ""


# evaluator signature: (verifier, cand_dir, ctx) -> ModuleResult
Evaluator = Callable[[Any, str, VerifierContext], ModuleResult]

_EVALUATORS: Dict[str, Evaluator] = {}
_MODULES_LOADED = False


class ModuleAPI:
    """Handed to a module's ``register(api)`` so it never imports core internals."""
    def register_verifier(self, kind: str, evaluator: Evaluator) -> None:
        register_verifier(kind, evaluator)


def register_verifier(kind: str, evaluator: Evaluator) -> None:
    """Register a module verifier kind (a string like 'statistical') + its evaluator.
    'hard'/'soft'/'human' are builtin and cannot be overridden."""
    if kind in ("hard", "soft", "human"):
        raise ValueError("'{}' is a builtin verifier kind".format(kind))
    _EVALUATORS[kind] = evaluator


def get_evaluator(kind: str) -> Optional[Evaluator]:
    return _EVALUATORS.get(kind)


def registered_kinds() -> List[str]:
    return sorted(_EVALUATORS)


def load_modules() -> List[str]:
    """Discover installed modules via the 'loophole.modules' entry-point group and
    call their register(api). Idempotent; failures in one module don't break others."""
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return registered_kinds()
    _MODULES_LOADED = True
    api = ModuleAPI()
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="loophole.modules") if hasattr(eps, "select") \
            else eps.get("loophole.modules", [])
        for ep in group:
            try:
                ep.load()(api)
            except Exception:
                pass
    except Exception:
        pass
    return registered_kinds()
