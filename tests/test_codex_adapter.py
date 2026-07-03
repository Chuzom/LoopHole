"""Phase 2 — CodexExecutor: parse `codex exec --json`'s JSONL into agent_step,
register as `codex`, default its own network+secret.

Fixture-driven (no live CLI needed to run these tests), but the fixtures below
are REAL transcripts captured from live `codex exec ... --json` runs against
codex-cli 0.80.0 (one via a ChatGPT-subscription account hitting a model-gating
error, one via `--oss --local-provider ollama` reaching a real success path with
token-by-token streaming) — not guessed shapes.
"""
from __future__ import annotations

import loophole.adapters as A
from loophole import executors as ex
from loophole.state import Task
from loophole.tools import ToolResult

# Captured verbatim from a real `codex exec ... --json` run (ChatGPT-subscription
# auth) that hit a model-gating error — including the plain-text log line codex
# interleaves on the same stream (not valid JSON; must be skipped).
_ERROR_TRANSCRIPT = "\n".join([
    '{"type":"thread.started","thread_id":"019f27d2-8fd7-7031-a604-eb491e412141"}',
    '{"type":"turn.started"}',
    '2026-07-03T11:52:19.444564Z ERROR codex_api::endpoint::responses: '
    'error=http 400 Bad Request: Some("{\\"detail\\":\\"model requires a newer '
    'version\\"}")',
    '{"type":"error","message":"{\\"detail\\":\\"The \'gpt-5.5\' model requires a '
    'newer version of Codex.\\"}"}',
    '{"type":"turn.failed","error":{"message":"The \'gpt-5.5\' model requires a '
    'newer version of Codex. Please upgrade to the latest app or CLI and try '
    'again."}}',
])

# Captured verbatim (trimmed) from a real success-path run via --oss/ollama:
# token-by-token agent_message chunks (each chunk carries its own leading
# whitespace), followed by turn.completed.
_SUCCESS_TRANSCRIPT = "\n".join([
    '{"type":"thread.started","thread_id":"019f27db-ba77-7520-aa1a-91913c0dcc61"}',
    '{"type":"turn.started"}',
    '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"To"}}',
    '{"type":"item.completed","item":{"id":"item_1","type":"agent_message","text":" create"}}',
    '{"type":"item.completed","item":{"id":"item_2","type":"agent_message","text":" add.py"}}',
    '{"type":"item.completed","item":{"id":"item_3","type":"command_execution",'
    '"command":"touch add.py"}}',
    '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":34}}',
])


def test_parse_codex_jsonl_success_reconstructs_text_and_steps():
    steps, final = A.parse_codex_jsonl(_SUCCESS_TRANSCRIPT)
    assert final == "To create add.py"          # chunks joined, whitespace preserved
    assert steps == ["command_execution"]        # non-agent_message items are steps


def test_parse_codex_jsonl_error_path_surfaces_message():
    steps, final = A.parse_codex_jsonl(_ERROR_TRANSCRIPT)
    assert steps == []
    assert "newer version of Codex" in final


def test_parse_codex_jsonl_skips_interleaved_log_lines():
    # the plain-text ERROR log line in _ERROR_TRANSCRIPT must not crash the parser
    steps, final = A.parse_codex_jsonl(_ERROR_TRANSCRIPT)
    assert isinstance(steps, list) and isinstance(final, str)


def test_parse_codex_jsonl_degrades_on_plain_output():
    steps, final = A.parse_codex_jsonl("just plain text, no json")
    assert steps == [] and final == ""


def test_codex_registered_with_defaults():
    ex.load_executors()
    assert "codex" in ex.registered_executors()
    e = ex.resolve_executor("codex", {})
    assert e.network_hosts == ("api.openai.com", "chatgpt.com", "auth.openai.com")
    assert e.pass_env == ("OPENAI_API_KEY",)
    assert e.trusted is True                     # matches ChatGPT-subscription auth


def test_codex_executor_config_overrides():
    e = ex.resolve_executor("codex", {"trusted": False, "shell_timeout": 42,
                                      "network_hosts": ("x.example.com",)})
    assert e.trusted is False
    assert e.shell_timeout == 42
    assert e.network_hosts == ("x.example.com",)


class _Ctx:
    def __init__(self):
        self.steps = []

    def step(self, tool, note=""):
        self.steps.append(note)


class _FakeBelt:
    def __init__(self, *a, **k):
        pass

    def run_shell(self, cmd):
        return ToolResult(True, _SUCCESS_TRANSCRIPT)


def test_run_streams_steps_and_summary_to_ctx(monkeypatch):
    monkeypatch.setattr(A, "Toolbelt", _FakeBelt)
    ctx = _Ctx()
    r = A.CodexExecutor({}).run(
        Task(id="t", goal_id="g", description="do it", status="running"), "/tmp", ctx)
    assert r.ok
    assert r.summary == "To create add.py"
    assert "command_execution" in ctx.steps


class _FailBelt:
    def __init__(self, *a, **k):
        pass

    def run_shell(self, cmd):
        return ToolResult(False, _ERROR_TRANSCRIPT)


def test_run_reports_failure_summary(monkeypatch):
    monkeypatch.setattr(A, "Toolbelt", _FailBelt)
    r = A.CodexExecutor({}).run(
        Task(id="t", goal_id="g", description="do it", status="running"), "/tmp", None)
    assert not r.ok
    assert "newer version of Codex" in r.summary


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
    A.CodexExecutor({}).run(
        Task(id="t", goal_id="g", description=malicious, status="running"), "/tmp", None)
    assert captured["cmd"] == "codex exec {} --json --sandbox workspace-write " \
        "--skip-git-repo-check".format(shlex.quote(malicious))
