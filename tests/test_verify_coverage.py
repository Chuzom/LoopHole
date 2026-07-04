"""ROADMAP E3.3 — `loophole run --verify-coverage`: the coverage-threshold
verifier wired into the CLI. Real pytest-cov run under the real sandbox
(pytest-cov is a loophole [dev] extra so this suite can exercise it for
real); only the planner is faked (no LLM needed).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from loophole.cli import main
from loophole.provider import Completion, Provider

pytest.importorskip("pytest_cov")


class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


# A tiny module with exactly one covered function and one uncovered one:
# real coverage is 50% (2 of 4 statements), giving a clean threshold to test
# both above and below.
_MODULE = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
_TEST = "from mymod import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"


def _git_ws_with_coverage_fixture():
    """A git repo pre-seeded with mymod.py + its (partial-coverage) test —
    the coverage verifier checks EXISTING code, so the fixture must already
    be committed, not produced by the (trivial, no-op) executor task."""
    ws = tempfile.mkdtemp(prefix="loophole_verify_cov_")
    with open(os.path.join(ws, "mymod.py"), "w") as f:
        f.write(_MODULE)
    with open(os.path.join(ws, "test_mymod.py"), "w") as f:
        f.write(_TEST)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "add", "-A"], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "-m", "init"], check=True, env=env)
    return ws


def _run(monkeypatch, tmp_path, extra_args):
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["marker.txt"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws_with_coverage_fixture()
    db = os.path.join(str(tmp_path), "state.db")
    json_path = os.path.join(str(tmp_path), "result.json")
    runner = CliRunner()
    r = runner.invoke(main, [
        "run", "verify-coverage test",
        "--executor-command", "python3 -c \"open('marker.txt','w').write('x')\"",
        "--skip-critique", "--no-watch", "--max-rounds", "2",
        "--workspace", ws, "--db", db, "--json-file", json_path, *extra_args,
    ])
    data = json.load(open(json_path)) if os.path.exists(json_path) else None
    return r, data


def test_verify_coverage_passes_below_actual_coverage(monkeypatch, tmp_path):
    # real coverage is 50%; a 40% threshold must pass
    r, data = _run(monkeypatch, tmp_path, [
        "--verify-coverage", "40", "--verify-coverage-target", "mymod",
    ])
    assert r.exit_code == 0, r.output
    assert data["verified_done"] is True


def test_verify_coverage_fails_above_actual_coverage(monkeypatch, tmp_path):
    # real coverage is 50%; a 90% threshold must fail
    r, data = _run(monkeypatch, tmp_path, [
        "--verify-coverage", "90", "--verify-coverage-target", "mymod",
    ])
    assert r.exit_code == 1, r.output
    assert data["verified_done"] is False


def test_verify_coverage_composes_with_verify(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, [
        "--verify", "true",
        "--verify-coverage", "40", "--verify-coverage-target", "mymod",
    ])
    assert r.exit_code == 0, r.output
    assert len(data["verified"]) == 2
    assert all(v["passed"] for v in data["verified"])


def test_verify_coverage_alone_skips_the_human_checkpoint(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, [
        "--verify-coverage", "40", "--verify-coverage-target", "mymod",
    ])
    assert r.exit_code == 0, r.output
    assert "human checkpoint" not in r.output.lower()


def test_verify_coverage_invalid_percent_is_a_usage_error(monkeypatch, tmp_path):
    r, _ = _run(monkeypatch, tmp_path, ["--verify-coverage", "150"])
    assert r.exit_code == 2, r.output
    assert "--verify-coverage" in r.output
