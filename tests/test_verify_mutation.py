"""ROADMAP E3.3 — `loophole run --verify-mutation`: the mutation-testing
verifier wired into the CLI. Real mutmut run under the real sandbox
(mutmut is a loophole [dev] extra, pinned below 3.x — 3.3.1 segfaulted on
every mutant in live testing, 2.5.1 worked correctly); only the planner is
faked (no LLM needed).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from loophole import sandbox
from loophole.cli import main
from loophole.provider import Completion, Provider

pytest.importorskip("mutmut")

# mutmut unconditionally opens a PTY (os.openpty()) to stream test output on
# every POSIX platform — verified live: this raises PermissionError under
# Seatbelt's deny-by-default profile, which doesn't grant PTY allocation (only
# fixed devices like /dev/tty are allowlisted, not allocating a fresh pty
# pair). bwrap's own minimal /dev (via --dev /dev) includes ptmx/devpts by
# convention, so this is expected to work under Linux/bwrap — same class of
# "verified correct, this specific sandbox can't run it" gap as the aider
# adapter's flags (verified against the real binary, no live run possible in
# an environment without an API key).
pytestmark = pytest.mark.skipif(
    sandbox.mechanism() == "seatbelt",
    reason="mutmut opens a PTY (os.openpty()) which Seatbelt's deny-by-default "
           "profile blocks; verified working under Linux/bwrap in CI (sandbox.yml)",
)


class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


# classify() has 4 mutants (boundary/branch flips). A test exercising both
# branches and the boundary kills all 4; a test exercising only one branch
# leaves survivors — verified live against real mutmut 2.5.1 before writing
# this (4/4 killed -> exit 0; 1/4 killed, 3 survivors -> exit 2).
_MODULE = (
    "def classify(n):\n"
    "    if n > 10:\n"
    "        return 'big'\n"
    "    else:\n"
    "        return 'small'\n"
)
_STRONG_TEST = (
    "from mymod import classify\n\n"
    "def test_classify_big():\n"
    "    assert classify(20) == 'big'\n\n"
    "def test_classify_small():\n"
    "    assert classify(5) == 'small'\n\n"
    "def test_classify_boundary():\n"
    "    assert classify(10) == 'small'\n"
    "    assert classify(11) == 'big'\n"
)
_WEAK_TEST = (
    "from mymod import classify\n\n"
    "def test_classify_big():\n"
    "    assert classify(20) == 'big'\n"
)


def _git_ws_with_fixture(test_source):
    ws = tempfile.mkdtemp(prefix="loophole_verify_mut_")
    os.makedirs(os.path.join(ws, "tests"))
    with open(os.path.join(ws, "mymod.py"), "w") as f:
        f.write(_MODULE)
    with open(os.path.join(ws, "tests", "test_mymod.py"), "w") as f:
        f.write(test_source)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "add", "-A"], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "-m", "init"], check=True, env=env)
    return ws


def _run(monkeypatch, tmp_path, test_source, extra_args):
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["marker.txt"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws_with_fixture(test_source)
    db = os.path.join(str(tmp_path), "state.db")
    json_path = os.path.join(str(tmp_path), "result.json")
    runner = CliRunner()
    r = runner.invoke(main, [
        "run", "verify-mutation test",
        "--executor-command", "python3 -c \"open('marker.txt','w').write('x')\"",
        "--skip-critique", "--no-watch", "--max-rounds", "2",
        "--workspace", ws, "--db", db, "--json-file", json_path, *extra_args,
    ])
    data = json.load(open(json_path)) if os.path.exists(json_path) else None
    return r, data


def test_verify_mutation_passes_when_all_mutants_killed(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, _STRONG_TEST, [
        "--verify-mutation", "mymod.py", "--verify-mutation-tests-dir", "tests",
    ])
    assert r.exit_code == 0, r.output
    assert data["verified_done"] is True


def test_verify_mutation_fails_when_mutants_survive(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, _WEAK_TEST, [
        "--verify-mutation", "mymod.py", "--verify-mutation-tests-dir", "tests",
    ])
    assert r.exit_code == 1, r.output
    assert data["verified_done"] is False


def test_verify_mutation_composes_with_verify(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, _STRONG_TEST, [
        "--verify", "true",
        "--verify-mutation", "mymod.py", "--verify-mutation-tests-dir", "tests",
    ])
    assert r.exit_code == 0, r.output
    assert len(data["verified"]) == 2
    assert all(v["passed"] for v in data["verified"])


def test_verify_mutation_alone_skips_the_human_checkpoint(monkeypatch, tmp_path):
    r, data = _run(monkeypatch, tmp_path, _STRONG_TEST, [
        "--verify-mutation", "mymod.py", "--verify-mutation-tests-dir", "tests",
    ])
    assert r.exit_code == 0, r.output
    assert "human checkpoint" not in r.output.lower()


def test_verify_mutation_rejects_empty_path_as_usage_error(monkeypatch, tmp_path):
    r, _ = _run(monkeypatch, tmp_path, _STRONG_TEST, ["--verify-mutation", ""])
    assert r.exit_code == 2, r.output
    assert "--verify-mutation" in r.output
