"""Cost governance (council critique #6).

A Budget threads through every provider call. At 80% of a hard ceiling it signals
that roles should auto-downgrade to cheaper models; at 100% it signals pause
(never crash). ``estimate`` does a cheap dry-run prediction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_cost_usd: float = 0.0       # 0 = unlimited
    max_tokens: int = 0             # 0 = unlimited
    spent_usd: float = 0.0
    spent_tokens: int = 0

    def charge(self, usd: float, tokens: int) -> None:
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


def estimate(goal: str, rounds: int, avg_tasks: int = 5,
             price_in: float = 0.003, price_out: float = 0.015) -> dict:
    """Very rough dry-run cost prediction.

    Assumes per round: 1 planner call + avg_tasks executor sessions
    (~6 LLM calls each) + 1 verify pass + occasional critique.
    """
    calls_per_round = 1 + avg_tasks * 6 + 1
    tok_in_per_call = 1500
    tok_out_per_call = 600
    total_calls = calls_per_round * rounds
    tok_in = total_calls * tok_in_per_call
    tok_out = total_calls * tok_out_per_call
    cost = (tok_in / 1000.0) * price_in + (tok_out / 1000.0) * price_out
    return {
        "rounds": rounds,
        "estimated_calls": total_calls,
        "estimated_tokens": tok_in + tok_out,
        "estimated_cost_usd": round(cost, 4),
        "note": "rough upper-bound; local (Ollama) models cost $0",
    }
