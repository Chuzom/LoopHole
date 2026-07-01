"""Planner — goal -> task DAG, with per-task read/write declarations.

The planner emits a JSON list of tasks. Each task declares ``reads`` and
``writes`` globs so the scheduler can parallelize only disjoint-write tasks
(council critique A). Output is validated as a DAG before it is ever persisted
(council critique #5); on failure the specific error is fed back to the planner
and it retries.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import List, Optional

from .contract import GoalContract
from .provider import Provider, ProviderError, Msg
from .scheduler import PlannedTask, validate_dag, DagError


PLANNER_SYSTEM = """You are the Planner for loophole, a multi-agent build system.
Decompose the GOAL into a directed acyclic graph (DAG) of small, concrete tasks.

Rules:
- Each task is independently executable by a tool-using agent (write_file, read_file, run_shell).
- Declare dependencies with `depends_on` (list of task ids that must finish first).
- Declare `reads` and `writes` as lists of file globs the task will read/write.
  Tasks with overlapping `writes` will NOT run in parallel, so keep write-sets tight and disjoint.
- Prefer many small tasks over a few big ones.
- The graph MUST be acyclic. Every id in `depends_on` must be a task id in your list.

Return ONLY a JSON array, no prose. Schema:
[
  {"id": "t1", "description": "...", "depends_on": [], "reads": [], "writes": ["src/foo.py"]},
  {"id": "t2", "description": "...", "depends_on": ["t1"], "reads": ["src/foo.py"], "writes": ["tests/test_foo.py"]}
]
"""


def _extract_json_array(text: str) -> list:
    text = text.strip()
    # strip code fences
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.M).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("planner did not return a JSON array")
    return json.loads(text[start:end + 1])


def plan_hash(tasks: List[PlannedTask]) -> str:
    """Structural hash: dependency-graph shape + write targets (council critique C).

    Independent of task wording — two reworded-but-identical plans hash the same.
    """
    shape = sorted(
        "{}<{}>:{}".format(
            sorted(t.writes),
            sorted(t.depends_on and [_norm(d) for d in t.depends_on] or []),
            len(t.reads),
        )
        for t in tasks
    )
    return hashlib.sha256("|".join(shape).encode()).hexdigest()[:16]


def _norm(s: str) -> str:
    return re.sub(r"\d+", "#", s)


def make_plan(provider: Provider, contract: GoalContract,
              feedback: Optional[str] = None, max_retries: int = 3) -> List[PlannedTask]:
    """Ask the planner for a DAG, validate it, retry with errors on failure."""
    user = "GOAL:\n{}\n".format(contract.goal)
    if contract.acceptance_criteria:
        user += "\nACCEPTANCE CRITERIA:\n- " + "\n- ".join(contract.acceptance_criteria)
    if contract.hard_verifiers:
        user += "\n\nThe work will be verified by: " + \
                "; ".join(v.command for v in contract.hard_verifiers if v.command)
    if feedback:
        user += "\n\nPREVIOUS ATTEMPT FAILED — fix this:\n" + feedback

    last_err = ""
    for attempt in range(max_retries):
        msgs = [Msg("system", PLANNER_SYSTEM),
                Msg("user", user + (("\n\nValidator error: " + last_err) if last_err else ""))]
        try:
            # inside the retry loop: a provider timeout/outage is retried like a
            # malformed plan, then surfaces as a clean PlannerError (never an
            # unhandled crash out of run_goal).
            comp = provider.complete(msgs, temperature=0.3)
            raw = _extract_json_array(comp.text)
            tasks = [
                PlannedTask(
                    id=str(t["id"]),
                    description=str(t.get("description", "")),
                    depends_on=[str(x) for x in t.get("depends_on", [])],
                    reads=[str(x) for x in t.get("reads", [])],
                    writes=[str(x) for x in t.get("writes", [])],
                )
                for t in raw
            ]
            if not tasks:
                raise ValueError("empty plan")
            validate_dag(tasks)
            return tasks
        except (ValueError, KeyError, DagError, json.JSONDecodeError, ProviderError) as e:
            last_err = str(e)
            continue
    raise PlannerError("planner failed to produce a valid DAG after {} attempts: {}"
                       .format(max_retries, last_err))


class PlannerError(RuntimeError):
    pass
