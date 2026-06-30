"""STRAT-1 — `loophole init` contract inference + CLI wiring."""
from __future__ import annotations

import json
import os
import tempfile

from click.testing import CliRunner

from loophole.cli import main
from loophole.contract import VerifierKind
from loophole.initializer import (detect_contract, write_starter, load_contract,
                                  CONTRACT_FILENAME, GOAL_TODO)


def _repo(files):
    d = tempfile.mkdtemp(prefix="loophole_init_")
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return d


# ---- detection -------------------------------------------------------------

def test_detect_pytest():
    repo = _repo({"pyproject.toml": "[tool.pytest.ini_options]\n",
                  "tests/test_a.py": "def test_x():\n    assert True\n",
                  "pkg/__init__.py": ""})
    c, notes = detect_contract(repo)
    hard = [v for v in c.verifiers if v.kind == VerifierKind.HARD]
    assert hard and hard[0].command == "pytest -q"
    assert hard[0].expected_test_delta == 0
    assert "pkg/**" in c.allowed_writes and "tests/**" in c.allowed_writes
    assert c.goal == GOAL_TODO


def test_detect_node():
    repo = _repo({"package.json": json.dumps({"scripts": {"test": "jest"}})})
    c, _ = detect_contract(repo)
    assert any(v.command == "npm test" for v in c.verifiers if v.kind == VerifierKind.HARD)


def test_detect_go_and_cargo():
    go = _repo({"main_test.go": "package main\n"})
    assert any(v.command == "go test ./..." for v in detect_contract(go)[0].verifiers)
    rust = _repo({"Cargo.toml": "[package]\nname='x'\n"})
    assert any(v.command == "cargo test" for v in detect_contract(rust)[0].verifiers)


def test_no_runner_adds_human_checkpoint():
    repo = _repo({"README.md": "# docs only\n"})
    c, _ = detect_contract(repo)
    assert any(v.kind == VerifierKind.HUMAN for v in c.verifiers)
    assert not any(v.kind == VerifierKind.HARD for v in c.verifiers)
    assert c.allowed_writes == ["**"]   # ambiguous layout -> permissive fallback


def test_protected_includes_ci_lockfiles_and_self():
    repo = _repo({".github/workflows/ci.yml": "on: push\n", "poetry.lock": "x\n",
                  "LICENSE": "MIT\n", "pyproject.toml": "[tool.pytest]\n"})
    c, _ = detect_contract(repo)
    assert ".github/workflows/**" in c.protected_paths
    assert "poetry.lock" in c.protected_paths
    assert "LICENSE" in c.protected_paths
    assert CONTRACT_FILENAME in c.protected_paths   # agent can't rewrite its contract


# ---- file round-trip -------------------------------------------------------

def test_write_and_load_roundtrip():
    repo = _repo({"pyproject.toml": "[tool.pytest]\n", "tests/test_a.py": "def test():\n pass\n"})
    c, _ = detect_contract(repo)
    out = os.path.join(repo, CONTRACT_FILENAME)
    write_starter(c, out)
    raw = json.load(open(out))
    assert raw["_help"]                       # guidance present
    loaded = load_contract(out)               # _help ignored on load
    assert loaded.goal == c.goal
    assert [v.command for v in loaded.hard_verifiers] == [v.command for v in c.hard_verifiers]


# ---- CLI -------------------------------------------------------------------

def test_cli_init_then_run_blocks_on_todo_goal():
    runner = CliRunner()
    with runner.isolated_filesystem():
        with open("pyproject.toml", "w") as f:
            f.write("[tool.pytest]\n")
        os.makedirs("tests")
        with open("tests/test_a.py", "w") as f:
            f.write("def test():\n assert True\n")

        r = runner.invoke(main, ["init"])
        assert r.exit_code == 0, r.output
        assert os.path.exists(CONTRACT_FILENAME)

        # init refuses to clobber without --force
        r2 = runner.invoke(main, ["init"])
        assert r2.exit_code != 0 and "already exists" in r2.output

        # bare `run` auto-discovers the file but the goal is still a TODO -> refuse
        r3 = runner.invoke(main, ["run"])
        assert r3.exit_code != 0
        assert "TODO" in r3.output
