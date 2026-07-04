"""ROADMAP E3.3 — `loophole run --verify-rubric`: the bundled rubric library
wired into the CLI as a soft (LLM-judge) verifier. Real merge gate/verifier
boundary — only the planner and judge are faked (no live LLM needed).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from click.testing import CliRunner

from loophole.cli import main
from loophole.provider import Completion, Provider


class _FakePlanner(Provider):
    name = "fake-planner"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


class _FakeJudge(Provider):
    """Stands in for the soft-verifier judge (roles.critic) — returns a fixed,
    clearly-parseable verdict instead of reasoning about the real snapshot."""
    name = "fake-judge"

    def __init__(self, satisfied):
        self._satisfied = satisfied

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=json.dumps(
            {"satisfied": self._satisfied, "reasons": ["fake judge verdict"]}))


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_verify_rubric_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def _run(monkeypatch, tmp_path, extra_args, satisfied):
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["x.txt"]}]

    def _make_provider(spec):
        return _FakeJudge(satisfied) if spec == "fake:judge" else _FakePlanner(tasks)

    monkeypatch.setattr("loophole.cli.make_provider", _make_provider)
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    json_path = os.path.join(str(tmp_path), "result.json")
    runner = CliRunner()
    r = runner.invoke(main, [
        "run", "verify-rubric test",
        "--executor-command", "python3 -c \"open('x.txt','w').write('x')\"",
        "--critic-model", "fake:judge",
        "--skip-critique", "--no-watch", "--max-rounds", "2",
        "--workspace", ws, "--db", db, "--json-file", json_path, *extra_args,
    ])
    data = json.load(open(json_path)) if os.path.exists(json_path) else None
    return r, data


def test_verify_rubric_alone_grants_via_human_when_judge_does_not_veto(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path,
                   ["--verify-rubric", "no-hardcoded-secrets"], satisfied=True)
    assert r.exit_code == 0, r.output
    assert data["verified_done"] is True


def test_verify_rubric_vetoes_a_hard_pass(monkeypatch, tmp_path):
    """A soft verifier can veto even when the hard verifier passes."""
    r, data = _run(monkeypatch, tmp_path, [
        "--verify", "true", "--verify-rubric", "no-stub-implementations",
    ], satisfied=False)
    assert r.exit_code == 1, r.output
    assert data["verified_done"] is False


def test_verify_rubric_composes_with_verify_when_satisfied(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, [
        "--verify", "true", "--verify-rubric", "no-stub-implementations",
    ], satisfied=True)
    assert r.exit_code == 0, r.output
    assert data["verified_done"] is True


def test_verify_rubric_substitutes_goal_placeholder(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path,
                   ["--verify", "true", "--verify-rubric", "matches-goal-scope"],
                   satisfied=True)
    assert r.exit_code == 0, r.output
    # evaluate_soft_verifier names the result after the (already-substituted)
    # rubric text — the goal string must appear, and the raw placeholder must not.
    names = [v["name"] for v in data["verified"]]
    assert any("verify-rubric test" in n for n in names)
    assert not any("{goal}" in n for n in names)


def test_cli_unknown_rubric_is_a_usage_error(monkeypatch, tmp_path):
    r, _ = _run(monkeypatch, tmp_path, ["--verify-rubric", "does-not-exist"], satisfied=True)
    assert r.exit_code == 2, r.output
    assert "--verify-rubric" in r.output
