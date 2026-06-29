"""Scheduling: DAG validation and dependency-aware admission.

Council critique #5: a plan must pass ``validate_dag`` BEFORE it is persisted —
cycles and dangling dependency ids are rejected with a specific error so the
planner can retry. The scheduler can therefore never deadlock on an
unresolvable dependency.

Council critique A: tasks parallelize only if their declared write-sets are
disjoint, so two concurrent worktrees never fight over the same files.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .state import Task


class DagError(ValueError):
    """Raised when a proposed plan is not a valid DAG."""


@dataclass
class PlannedTask:
    id: str
    description: str
    depends_on: List[str]
    reads: List[str]
    writes: List[str]


def validate_dag(tasks: Sequence[PlannedTask]) -> None:
    """Reject cycles and dangling dependency ids. Raises DagError with detail."""
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise DagError("duplicate task ids in plan")
    idset = set(ids)
    for t in tasks:
        for dep in t.depends_on:
            if dep not in idset:
                raise DagError(
                    "task {!r} depends on unknown id {!r} (dangling reference)"
                    .format(t.id, dep))
    # Kahn's algorithm for cycle detection
    indeg: Dict[str, int] = {t.id: 0 for t in tasks}
    adj: Dict[str, List[str]] = {t.id: [] for t in tasks}
    for t in tasks:
        for dep in t.depends_on:
            adj[dep].append(t.id)
            indeg[t.id] += 1
    queue = [tid for tid, d in indeg.items() if d == 0]
    seen = 0
    while queue:
        n = queue.pop()
        seen += 1
        for m in adj[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(tasks):
        remaining = [tid for tid, d in indeg.items() if d > 0]
        raise DagError("cycle detected among tasks: {}".format(remaining))


def ready_tasks(tasks: List[Task]) -> List[Task]:
    """Tasks whose every dependency is 'done' and that are still pending."""
    done = {t.id for t in tasks if t.status == "done"}
    out = []
    for t in tasks:
        if t.status != "pending":
            continue
        if all(dep in done for dep in t.depends_on):
            out.append(t)
    return out


def _writes_overlap(a: Sequence[str], b: Sequence[str]) -> bool:
    """Two write-sets overlap if any glob from one matches a glob/literal from the other."""
    if not a or not b:
        # an undeclared write-set is treated as "writes everything" -> conservative
        return True
    for ga in a:
        for gb in b:
            if ga == gb or fnmatch.fnmatch(gb, ga) or fnmatch.fnmatch(ga, gb):
                return True
    return False


def admit_parallel(candidates: List[Task], max_parallel: int) -> List[Task]:
    """Pick a disjoint-write-set batch of ready tasks to run concurrently.

    Greedy: take tasks in order, admitting one only if its write-set does not
    overlap any already-admitted task's write-set (council critique A).
    """
    admitted: List[Task] = []
    for t in candidates:
        if len(admitted) >= max_parallel:
            break
        if any(_writes_overlap(t.writes, a.writes) for a in admitted):
            continue
        admitted.append(t)
    # always make progress: if nothing was admitted (all overlap), run one
    if not admitted and candidates:
        admitted.append(candidates[0])
    return admitted
