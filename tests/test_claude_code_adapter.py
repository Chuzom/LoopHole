"""Phase 2 — ClaudeCodeExecutor: parse Claude Code's stream-json into agent_step,
register as `claude-code`, default its own network+secret. Fixture-driven (no live CLI)."""
from __future__ import annotations

import json

import loophole.adapters as A
from loophole import executors as ex
from loophole.state import Task
from loophole.tools import ToolResult

_TRANSCRIPT = "\n".join([
    "some non-json preamble line",
    json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write"}]}}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "thinking"}]}}),
    json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}),
    json.dumps({"type": "result", "result": "created add.py, tests pass"}),
])


def test_parse_stream_json():
    steps, final = A.parse_stream_json(_TRANSCRIPT)
    assert steps == ["Write", "Bash"]
    assert "add.py" in final


def test_parse_stream_json_degrades_on_plain_output():
    steps, final = A.parse_stream_json("just plain text, no json")
    assert steps == [] and final == ""


def test_claude_code_registered_with_defaults():
    ex.load_executors()
    assert "claude-code" in ex.registered_executors()
    e = ex.resolve_executor("claude-code", {})
    assert e.network_hosts == ("api.anthropic.com",)
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
        return ToolResult(True, _TRANSCRIPT)


def test_run_streams_tool_steps_to_ctx(monkeypatch):
    monkeypatch.setattr(A, "Toolbelt", _FakeBelt)
    ctx = _Ctx()
    r = A.ClaudeCodeExecutor({}).run(
        Task(id="t", goal_id="g", description="do it", status="running"), "/tmp", ctx)
    assert r.ok and "add.py" in r.summary
    assert "Write" in ctx.steps and "Bash" in ctx.steps    # streamed to the FORGE
