"""Phase 0 — Executor SDK seam: registry, ExecContext step-emission, backward
compatibility, and a registered adapter driving a real run via --executor."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest

from loophole import executors as ex
from loophole.budget import Budget
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.executor import ExecContext, ExecResult, Executor
from loophole.loop import LoopConfig, Roles, run_goal
from loophole.provider import Completion, Provider
from loophole.state import Store, Task


class _Toy(Executor):
    def run(self, task, worktree, ctx=None):
        if ctx is not None:
            ctx.step("toy-tool", "did a thing")
        return ExecResult(ok=True, summary="toy ok", steps=1)


def _store():
    return Store(os.path.join(tempfile.mkdtemp(prefix="loophole_exsdk_"), "s.db"))


def _cjson(goal="g", verifier="true"):
    return GoalContract(goal=goal, verifiers=[
        Verifier(kind=VerifierKind.HARD, command=verifier)]).to_json()


# ---- registry --------------------------------------------------------------

def test_register_resolve_and_errors():
    ex._ADAPTERS.pop("toy", None)
    ex.register_executor("toy", lambda cfg: _Toy())
    assert "toy" in ex.registered_executors()
    assert isinstance(ex.resolve_executor("toy", {}), _Toy)
    with pytest.raises(ValueError):
        ex.resolve_executor("does-not-exist")
    with pytest.raises(ValueError):
        ex.register_executor("react", lambda c: _Toy())     # builtin name protected
    ex._ADAPTERS.pop("toy", None)


# ---- ExecContext emits agent_step ------------------------------------------

def test_exec_context_emits_agent_step():
    s = _store()
    gid = s.create_goal(_cjson(), "/ws")
    ctx = ExecContext(store=s, goal_id=gid, task_id="t1")
    _Toy().run(Task(id="t1", goal_id=gid, description="d", status="running"), "/tmp", ctx)
    steps = [e for e in s.events(gid) if e["kind"] == "agent_step"]
    assert steps and json.loads(steps[0]["payload"])["tool"] == "toy-tool"
    s.close()


def test_run_is_backward_compatible_two_args():
    # old 2-arg call still works — ctx is optional
    r = _Toy().run(Task(id="x", goal_id="g", description="d", status="running"), "/tmp")
    assert r.ok


# ---- CLI -------------------------------------------------------------------

def test_executor_list_shows_adapter_section():
    from click.testing import CliRunner
    from loophole.cli import main
    out = CliRunner().invoke(main, ["executor", "list"]).output
    assert "framework adapters" in out and "--executor" in out


# ---- end-to-end: a registered adapter drives a real run --------------------

class _WriteAdd(Executor):
    def run(self, task, worktree, ctx=None):
        if ctx is not None:
            ctx.step("write_file", "add.py")
        with open(os.path.join(worktree, "add.py"), "w") as f:
            f.write("def add(a, b):\n    return a + b\n")
        return ExecResult(ok=True, summary="wrote add.py", steps=1)


class _Planner(Provider):
    name = "p"

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=json.dumps([{"id": "t1", "description": "add.py",
            "depends_on": [], "reads": [], "writes": ["add.py"]}]))


def test_adapter_drives_real_run_via_executor_name():
    ex._ADAPTERS.pop("toy-add", None)
    ex.register_executor("toy-add", lambda cfg: _WriteAdd())
    ws = tempfile.mkdtemp(prefix="loophole_exrun_")
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "i"],
                   check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t",
                   "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t"})
    contract = GoalContract(goal="add", verifiers=[Verifier(kind=VerifierKind.HARD,
        command='/usr/bin/python3 -c "from add import add; assert add(2,3)==5; print(1)"')],
        allowed_writes=["**"], max_rounds=4, timeout_seconds=90)
    s = Store(os.path.join(ws, ".db"))
    gid = s.create_goal(contract.to_json(), ws)
    cfg = LoopConfig(max_parallel=1, skip_plan_critique=True, executor_name="toy-add")
    p = _Planner()
    outcome = run_goal(s, gid, contract, Roles(p, p, p), Budget(), cfg)
    kinds = [e["kind"] for e in s.events(gid)]
    s.close()
    ex._ADAPTERS.pop("toy-add", None)
    assert outcome.status == "done"                 # adapter's work reached verified DONE
    assert "agent_step" in kinds                    # its internal step streamed to the log
