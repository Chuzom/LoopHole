"""Phase 2 — AiderExecutor: register as `aider`, default its own network+secret,
force --no-auto-commits so loophole (not aider) owns the commit.

UNVERIFIED end-to-end: this environment has no LLM API key aider can use, so no
live task completion could be observed. Every flag in DEFAULT_CMD was confirmed
against the real aider 0.82.3 `--help` output (no fabricated flags) — including
catching that the codebase's OWN pre-existing --executor-command hint used the
stale `--yes` flag, which the current binary actually rejects.
"""
from __future__ import annotations

import loophole.adapters as A
from loophole import executors as ex
from loophole.state import Task
from loophole.tools import ToolResult


def test_aider_registered_with_defaults():
    ex.load_executors()
    assert "aider" in ex.registered_executors()
    e = ex.resolve_executor("aider", {})
    assert e.network_hosts == ("api.openai.com",)
    assert e.pass_env == ("OPENAI_API_KEY",)
    # unlike claude-code/codex: aider has no credential STORE to protect, so it
    # stays sandboxed by default (safer) rather than trusted-by-default
    assert e.trusted is False


def test_default_command_forces_no_auto_commits():
    # required for correctness, not cosmetic: see the class docstring / commit msg
    assert "--no-auto-commits" in A.AiderExecutor.DEFAULT_CMD
    assert "--yes-always" in A.AiderExecutor.DEFAULT_CMD
    assert "--yes " not in A.AiderExecutor.DEFAULT_CMD + " "   # the stale/rejected flag


def test_aider_executor_config_overrides():
    e = ex.resolve_executor("aider", {"trusted": True, "shell_timeout": 42,
                                      "pass_env": ("ANTHROPIC_API_KEY",)})
    assert e.trusted is True
    assert e.shell_timeout == 42
    assert e.pass_env == ("ANTHROPIC_API_KEY",)


class _Ctx:
    def __init__(self):
        self.steps = []

    def step(self, tool, note=""):
        self.steps.append(note)


class _FakeBelt:
    def __init__(self, *a, **k):
        pass

    def run_shell(self, cmd):
        return ToolResult(True, "Applied edit to add.py\n\nadd.py\n"
                                "def add(a, b):\n    return a + b\n")


def test_run_reports_ok_and_tail_summary(monkeypatch):
    monkeypatch.setattr(A, "Toolbelt", _FakeBelt)
    ctx = _Ctx()
    r = A.AiderExecutor({}).run(
        Task(id="t", goal_id="g", description="do it", status="running"), "/tmp", ctx)
    assert r.ok
    assert "return a + b" in r.summary
    assert ctx.steps == ["start"]     # aider has no step-stream, unlike claude-code/codex


class _FailBelt:
    def __init__(self, *a, **k):
        pass

    def run_shell(self, cmd):
        return ToolResult(False, "")


def test_run_failure_fallback_summary(monkeypatch):
    monkeypatch.setattr(A, "Toolbelt", _FailBelt)
    r = A.AiderExecutor({}).run(
        Task(id="t", goal_id="g", description="do it", status="running"), "/tmp", None)
    assert not r.ok
    assert r.summary == "aider failed"


def test_task_description_is_shell_escaped(monkeypatch):
    import shlex
    captured = {}

    class _Belt:
        def __init__(self, *a, **k):
            pass

        def run_shell(self, cmd):
            captured["cmd"] = cmd
            return ToolResult(True, "")

    monkeypatch.setattr(A, "Toolbelt", _Belt)
    malicious = "a task; rm -rf /"
    A.AiderExecutor({}).run(
        Task(id="t", goal_id="g", description=malicious, status="running"), "/tmp", None)
    assert captured["cmd"] == A.AiderExecutor.DEFAULT_CMD.replace(
        "{task}", shlex.quote(malicious))
