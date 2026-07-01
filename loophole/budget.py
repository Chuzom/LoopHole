"""Cost governance (council critique #6).

A Budget threads through every provider call. At 80% of a hard ceiling it signals
that roles should auto-downgrade to cheaper models; at 100% it signals pause
(never crash). ``estimate`` does a cheap dry-run prediction.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Optional


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_cost_usd: float = 0.0       # 0 = unlimited
    max_tokens: int = 0             # 0 = unlimited
    spent_usd: float = 0.0
    spent_tokens: int = 0

    def __post_init__(self) -> None:
        # ARCH-3: charge() runs concurrently across the executor pool; guard the
        # read-modify-write so spend isn't undercounted and ceilings aren't overrun.
        self._lock = threading.Lock()

    def charge(self, usd: float, tokens: int) -> None:
        with self._lock:
            self.spent_usd += usd
            self.spent_tokens += tokens

    @property
    def cost_fraction(self) -> float:
        if self.max_cost_usd <= 0:
            return 0.0
        return self.spent_usd / self.max_cost_usd

    @property
    def token_fraction(self) -> float:
        if self.max_tokens <= 0:
            return 0.0
        return self.spent_tokens / self.max_tokens

    @property
    def fraction(self) -> float:
        return max(self.cost_fraction, self.token_fraction)

    @property
    def should_downgrade(self) -> bool:
        """True at >=80% of any ceiling — switch roles to cheaper models."""
        return self.fraction >= 0.8

    @property
    def exhausted(self) -> bool:
        """True at >=100% of any ceiling — pause."""
        return self.fraction >= 1.0

    def check(self) -> None:
        if self.exhausted:
            raise BudgetExceeded(
                "budget exhausted: ${:.4f}/{} usd, {}/{} tokens".format(
                    self.spent_usd, self.max_cost_usd or "inf",
                    self.spent_tokens, self.max_tokens or "inf"))

    def summary(self) -> str:
        return "spent ${:.4f}{}, {} tokens{}".format(
            self.spent_usd,
            "/{:.2f}".format(self.max_cost_usd) if self.max_cost_usd else "",
            self.spent_tokens,
            "/{}".format(self.max_tokens) if self.max_tokens else "")


def _historical_per_round(store: Any) -> Optional[dict]:
    """Median per-round spend from past runs' ``run_spend`` events, or None
    when there is no usable history."""
    try:
        goals = list(store.list_goals())
    except Exception:
        return None
    tokens, usd = [], []
    for g in goals:
        for e in store.events(g["id"]):
            if e["kind"] != "run_spend":
                continue
            try:
                p = json.loads(e["payload"] or "{}")
            except (TypeError, ValueError):
                continue
            r, t = p.get("rounds") or 0, p.get("spent_tokens") or 0
            if r > 0 and t > 0:
                tokens.append(t / r)
                usd.append((p.get("spent_usd") or 0.0) / r)
    if not tokens:
        return None
    tokens.sort()
    usd.sort()
    mid = len(tokens) // 2
    return {"tokens_per_round": tokens[mid], "usd_per_round": usd[mid],
            "runs": len(tokens)}


def estimate(goal: str, rounds: int, avg_tasks: int = 5,
             price_in: float = 0.003, price_out: float = 0.015,
             store: Any = None) -> dict:
    """Dry-run cost prediction (no model calls).

    Grounded when possible: with a ``store``, uses the median per-round spend
    of past runs on this machine (``run_spend`` events). Falls back to a rough
    fixed heuristic — per round: 1 planner call + avg_tasks executor sessions
    (~6 LLM calls each) + 1 verify pass.
    """
    hist = _historical_per_round(store) if store is not None else None
    calls_per_round = 1 + avg_tasks * 6 + 1
    total_calls = calls_per_round * rounds
    if hist:
        return {
            "rounds": rounds,
            "estimated_calls": total_calls,
            "estimated_tokens": int(hist["tokens_per_round"] * rounds),
            "estimated_cost_usd": round(hist["usd_per_round"] * rounds, 4),
            "basis": "history",
            "note": "grounded in the median per-round spend of {} past run(s) "
                    "on this machine".format(hist["runs"]),
        }
    tok_in = total_calls * 1500
    tok_out = total_calls * 600
    cost = (tok_in / 1000.0) * price_in + (tok_out / 1000.0) * price_out
    return {
        "rounds": rounds,
        "estimated_calls": total_calls,
        "estimated_tokens": tok_in + tok_out,
        "estimated_cost_usd": round(cost, 4),
        "basis": "heuristic",
        "note": "rough upper-bound; local (Ollama) models cost $0",
    }
