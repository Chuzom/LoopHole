"""Phase 4 — the example adapter package proves a third-party framework adapter works
through the loophole.executors contract (register -> resolve -> run -> agent_step)."""
from __future__ import annotations

import os
import sys
import tempfile

_EX = os.path.join(os.path.dirname(__file__), "..", "examples", "adapter_package")
sys.path.insert(0, _EX)

from loophole import executors as ex
from loophole.state import Task


class _Ctx:
    def __init__(self):
        self.steps = []

    def step(self, tool, note=""):
        self.steps.append((tool, note))


def test_example_adapter_registers_resolves_and_runs():
    from loophole_agent_echo.plugin import EchoExecutor, register
    ex._ADAPTERS.pop("echo", None)
    register(ex.ExecutorAPI())
    e = ex.resolve_executor("echo", {})
    assert isinstance(e, EchoExecutor)

    wt = tempfile.mkdtemp(prefix="loophole_echo_")
    ctx = _Ctx()
    r = e.run(Task(id="t", goal_id="g", description="write the notes", status="running"),
              wt, ctx)
    assert r.ok and os.path.exists(os.path.join(wt, "notes.md"))   # did real work in the worktree
    assert ctx.steps and ctx.steps[0][0] == "echo"                 # streamed a step
    ex._ADAPTERS.pop("echo", None)
