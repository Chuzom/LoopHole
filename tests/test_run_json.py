"""ROADMAP E1.1 — `loophole run --json` / `report.to_json`: the machine-readable
CI contract the Action and PR-comment tooling consume.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from loophole.cli import main
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.provider import Completion, Provider


def _schema():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, "loophole", "schemas", "run_result.schema.json")
    with open(path) as f:
        return json.load(f)


class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_json_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def _run_cli(monkeypatch, tmp_path, verify_cmd, executor_command, extra_args=()):
    tasks = [{"id": "t1", "description": "create add.py with add(a,b)",
             "depends_on": [], "reads": [], "writes": ["add.py"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    runner = CliRunner()
    result = runner.invoke(main, [
        "run", "implement add(a,b) in add.py returning a+b",
        "--verify", verify_cmd, "--workspace", ws,
        "--executor-command", executor_command, "--skip-critique",
        "--no-watch", "--max-rounds", "2", "--db", db, *extra_args,
    ])
    return result


_WRITE_ADD = "python3 -c \"open('add.py','w').write('def add(a,b): return a+b')\""
_WRITE_WRONG = "python3 -c \"open('add.py','w').write('def add(a,b): return a-b')\""
_VERIFY_ADD = 'python3 -c "from add import add; assert add(2,3)==5; print(\'ok\')"'


def _extract_json(output: str) -> dict:
    # the human report precedes the JSON block; find the last top-level object
    start = output.rindex("\n{\n")
    return json.loads(output[start:])


def test_json_schema_is_valid_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.Draft202012Validator.check_schema(_schema())


def test_verified_done_json_matches_schema(monkeypatch, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    r = _run_cli(monkeypatch, tmp_path, _VERIFY_ADD, _WRITE_ADD, extra_args=["--json"])
    assert r.exit_code == 0, r.output
    data = _extract_json(r.output)
    jsonschema.validate(data, _schema())
    assert data["status"] == "done"
    assert data["exit_code"] == 0
    assert data["verified_done"] is True
    assert data["schema_version"] == 1
    assert any(v["passed"] for v in data["verified"])
    assert data["budget"]["tokens"] >= 0


def test_failing_run_json_matches_schema(monkeypatch, tmp_path):
    jsonschema = pytest.importorskip("jsonschema")
    r = _run_cli(monkeypatch, tmp_path, _VERIFY_ADD, _WRITE_WRONG, extra_args=["--json"])
    assert r.exit_code == 1, r.output
    data = _extract_json(r.output)
    jsonschema.validate(data, _schema())
    assert data["status"] in ("failed", "paused")
    assert data["exit_code"] == 1
    assert data["verified_done"] is False


def test_json_file_written(monkeypatch, tmp_path):
    out_path = os.path.join(str(tmp_path), "result.json")
    r = _run_cli(monkeypatch, tmp_path, _VERIFY_ADD, _WRITE_ADD,
                extra_args=["--json-file", out_path])
    assert r.exit_code == 0, r.output
    with open(out_path) as f:
        data = json.load(f)
    assert data["verified_done"] is True
    assert data["goal_id"]


def test_not_proven_matches_between_json_and_text():
    """The JSON's not_proven[] and the text report's residual-risk bullets must
    stay in sync — both are derived from the same _not_proven() helper."""
    from loophole.report import _not_proven, residual_risk_report
    from loophole.budget import Budget
    c = GoalContract(goal="x", verifiers=[Verifier(kind=VerifierKind.HARD, command="true")])
    bullets = _not_proven(c, "done")
    text = residual_risk_report(c, None, "done", 1, Budget().summary())
    for b in bullets:
        assert b in text
