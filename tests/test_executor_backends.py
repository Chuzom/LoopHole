"""VIS-1 — pluggable executor backends.

The worker is swappable; the verifier boundary is unchanged. These tests cover the
CommandExecutor (bring-your-own external agent) end to end, the injection-safety of
the task interpolation, and that the ReAct shim still works via a fake provider.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from loophole import sandbox
from loophole.executor import (Executor, ReActExecutor, CommandExecutor,
                               execute_task, ExecResult)
from loophole.provider import Provider, Completion, ToolCall
from loophole.state import Task


def _task(desc="do the thing"):
    return Task(id="t1", goal_id="g1", description=desc)


requires_sandbox = pytest.mark.skipif(
    not sandbox.available(), reason="no OS sandbox on this host")


# ---- CommandExecutor (external agent) --------------------------------------

@requires_sandbox
def test_command_executor_runs_external_agent_in_worktree():
    with tempfile.TemporaryDirectory() as wt:
        # the "external agent" is just a shell command that produces a file
        ex = CommandExecutor("echo built > out.txt")
        res = ex.run(_task(), wt)
        assert isinstance(res, ExecResult) and res.ok
        assert os.path.exists(os.path.join(wt, "out.txt"))
        # TASK.md is surfaced for the agent to READ during the run, then removed so it
        # never counts as agent output (write-allowlist) or pollutes the merge.
        assert not os.path.exists(os.path.join(wt, "TASK.md"))


@requires_sandbox
def test_command_executor_quotes_task_no_injection():
    with tempfile.TemporaryDirectory() as wt:
        # malicious task text tries to inject a second command
        ex = CommandExecutor("echo {task} > echoed.txt")
        res = ex.run(_task("hello; touch pwned"), wt)
        assert res.ok
        # the injected `touch pwned` must NOT have run (task was shell-quoted)
        assert not os.path.exists(os.path.join(wt, "pwned"))
        with open(os.path.join(wt, "echoed.txt")) as f:
            assert "hello; touch pwned" in f.read()


@requires_sandbox
def test_command_executor_reports_failure_on_nonzero():
    with tempfile.TemporaryDirectory() as wt:
        res = CommandExecutor("exit 3").run(_task(), wt)
        assert not res.ok


def test_command_executor_is_an_executor():
    assert issubclass(CommandExecutor, Executor)
    assert issubclass(ReActExecutor, Executor)


# ---- ReAct shim still works -------------------------------------------------

class _FakeProvider(Provider):
    name = "fake"
    price_in = 0.0
    price_out = 0.0

    def __init__(self):
        self._calls = 0

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        self._calls += 1
        if self._calls == 1:
            return Completion(text="working", tool_calls=[ToolCall(
                id="c1", name="write_file",
                arguments={"path": "made.txt", "content": "hi"})])
        return Completion(text="TASK_COMPLETE wrote the file")


@requires_sandbox
def test_react_executor_shim_still_works():
    with tempfile.TemporaryDirectory() as wt:
        res = execute_task(_FakeProvider(), _task(), wt, max_steps=4)
        assert res.ok
        assert os.path.exists(os.path.join(wt, "made.txt"))
