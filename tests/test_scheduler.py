from __future__ import annotations

import pytest

from loophole.scheduler import (PlannedTask, validate_dag, DagError,
                                ready_tasks, admit_parallel)
from loophole.state import Task


def pt(id, deps=None, writes=None):
    return PlannedTask(id=id, description=id, depends_on=deps or [],
                       reads=[], writes=writes or [])


def test_valid_dag_passes():
    validate_dag([pt("a"), pt("b", ["a"]), pt("c", ["a", "b"])])


def test_cycle_rejected():
    with pytest.raises(DagError) as e:
        validate_dag([pt("a", ["b"]), pt("b", ["a"])])
    assert "cycle" in str(e.value)


def test_dangling_dependency_rejected():
    with pytest.raises(DagError) as e:
        validate_dag([pt("a", ["ghost"])])
    assert "dangling" in str(e.value) or "unknown" in str(e.value)


def test_duplicate_ids_rejected():
    with pytest.raises(DagError):
        validate_dag([pt("a"), pt("a")])


def _task(id, status="pending", deps=None, writes=None):
    return Task(id=id, goal_id="g", description=id, status=status,
                depends_on=deps or [], writes=writes or [])


def test_ready_tasks_respects_deps():
    tasks = [_task("a", "done"), _task("b", deps=["a"]), _task("c", deps=["b"])]
    rdy = ready_tasks(tasks)
    assert [t.id for t in rdy] == ["b"]


def test_admit_parallel_disjoint_writes():
    cands = [_task("a", writes=["src/x.py"]), _task("b", writes=["src/y.py"]),
             _task("c", writes=["src/x.py"])]
    admitted = admit_parallel(cands, max_parallel=4)
    ids = {t.id for t in admitted}
    # a and b are disjoint; c overlaps a -> excluded this batch
    assert "a" in ids and "b" in ids and "c" not in ids


def test_admit_parallel_always_progresses():
    cands = [_task("a", writes=["src/x.py"]), _task("b", writes=["src/x.py"])]
    admitted = admit_parallel(cands, max_parallel=4)
    assert len(admitted) >= 1
