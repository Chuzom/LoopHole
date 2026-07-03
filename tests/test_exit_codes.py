"""ROADMAP E0.2 — stable, documented `loophole run`/`resume` exit codes.

  0 = verified done
  1 = not done (paused / failed / budget exhausted) — the run EXECUTED
  2 = usage/config error — the run never started

These are a CI contract: scripts branch on them, so once set they must not
silently change meaning.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from loophole.cli import main, UsageError
from loophole.provider import Completion, Provider


def test_usage_error_is_exit_code_2():
    assert UsageError.exit_code == 2
    assert issubclass(UsageError, Exception)


# ---- exit 2: usage/config errors (the run never started) -------------------

def test_no_goal_no_contract_exits_2():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["run"])
        assert r.exit_code == 2, r.output
        assert "provide a GOAL" in r.output


def test_missing_contract_file_exits_2():
    r = CliRunner().invoke(main, ["run", "--contract", "/nope/does-not-exist.json"])
    assert r.exit_code == 2, r.output


def test_todo_goal_contract_exits_2():
    runner = CliRunner()
    with runner.isolated_filesystem():
        runner.invoke(main, ["init"])                        # leaves goal as TODO
        r = runner.invoke(main, ["run"])
        assert r.exit_code == 2, r.output
        assert "TODO" in r.output


def test_bad_provider_spec_exits_2():
    r = CliRunner().invoke(main, ["run", "hello", "--verify", "true",
                                  "--planner-model", "not-a-real-provider:x"])
    assert r.exit_code == 2, r.output


def test_resume_unknown_goal_exits_2():
    r = CliRunner().invoke(main, ["resume", "goal-does-not-exist"])
    assert r.exit_code == 2, r.output
    assert "no such goal" in r.output


# ---- exit 0 / 1: the run executed -------------------------------------------

class _FakePlanner(Provider):
    """Deterministic stand-in for the planning LLM (no network/LLM needed)."""
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_exitcode_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def _run_with_fake_provider(monkeypatch, tmp_path, verify_cmd, executor_command):
    """Drive the real `run` CLI command end to end with make_provider stubbed to
    a deterministic fake — exercises the actual exit-code path, not run_goal()
    directly."""
    tasks = [{"id": "t1", "description": "create add.py with add(a,b)",
             "depends_on": [], "reads": [], "writes": ["add.py"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    runner = CliRunner()
    return runner.invoke(main, [
        "run", "implement add(a,b) in add.py returning a+b",
        "--verify", verify_cmd, "--workspace", ws,
        "--executor-command", executor_command, "--skip-critique",
        "--no-watch", "--max-rounds", "2", "--db", db,
    ])


def test_verified_done_exits_0(monkeypatch, tmp_path):
    write_add = "python3 -c \"open('add.py','w').write('def add(a,b): return a+b')\""
    verify = ('python3 -c "from add import add; assert add(2,3)==5; print(\'ok\')"')
    r = _run_with_fake_provider(monkeypatch, tmp_path, verify, write_add)
    assert r.exit_code == 0, r.output
    assert "VERIFIED DONE" in r.output or "Outcome: DONE" in r.output


def test_failing_verifier_exits_1(monkeypatch, tmp_path):
    write_wrong = "python3 -c \"open('add.py','w').write('def add(a,b): return a-b')\""
    verify = ('python3 -c "from add import add; assert add(2,3)==5"')
    r = _run_with_fake_provider(monkeypatch, tmp_path, verify, write_wrong)
    assert r.exit_code == 1, r.output
