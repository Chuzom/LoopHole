"""ROADMAP E3.1 — `loophole init --from-ci`: infer the verifier from the repo's
OWN GitHub Actions workflows (ground truth) instead of guessing from file
presence, with graceful fallback to the existing heuristic.
"""
from __future__ import annotations

import os
import tempfile

from click.testing import CliRunner

from loophole.cli import main
from loophole.contract import VerifierKind
from loophole.initializer import (_workflow_run_lines, detect_ci_test_command,
                                  detect_contract)


def _repo(files):
    d = tempfile.mkdtemp(prefix="loophole_from_ci_")
    for rel, content in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p) or d, exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    return d


# ---- _workflow_run_lines ------------------------------------------------------

def test_single_line_run_step():
    text = "steps:\n  - run: pytest -q\n"
    assert list(_workflow_run_lines(text)) == ["pytest -q"]


def test_block_scalar_run_step_stops_at_dedent():
    text = (
        "steps:\n"
        "  - name: Run the full suite\n"
        "    run: |\n"
        "      python -m pip install -U pip\n"
        "      pytest -q\n"
        "  - name: Next step\n"
        "    run: echo done\n"
    )
    lines = list(_workflow_run_lines(text))
    assert lines == ["python -m pip install -U pip", "pytest -q", "echo done"]


def test_inline_ampersand_chained_commands_split():
    text = "steps:\n  - run: cd backend && pytest -q\n"
    assert list(_workflow_run_lines(text)) == ["cd backend", "pytest -q"]


def test_no_run_steps_yields_nothing():
    assert list(_workflow_run_lines("name: ci\non: [push]\n")) == []


# ---- detect_ci_test_command ---------------------------------------------------

# Modeled closely on loophole's OWN real .github/workflows/ci.yml for realism.
_REAL_SHAPED_CI = """\
name: ci
on:
  push:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - name: Install package + test deps
        run: |
          python -m pip install -U pip
          pip install -e '.[dev]'
      - name: Run the full suite
        run: pytest -q
"""


def test_finds_pytest_in_realistic_workflow():
    repo = _repo({".github/workflows/ci.yml": _REAL_SHAPED_CI})
    notes = []
    cmd = detect_ci_test_command(repo, notes)
    assert cmd == "pytest -q"
    assert any("ci.yml" in n and "pytest -q" in n for n in notes)


def test_no_workflows_dir_returns_none():
    repo = _repo({"README.md": "hello"})
    assert detect_ci_test_command(repo, []) is None


def test_workflow_with_no_recognizable_test_command_returns_none():
    # e.g. a Java/Gradle-only workflow — ecosystem this doesn't recognize (yet)
    repo = _repo({".github/workflows/ci.yml": (
        "on: [push]\njobs:\n  build:\n    steps:\n"
        "      - run: mvn -B install\n"
    )})
    assert detect_ci_test_command(repo, []) is None


def test_finds_node_test_script():
    repo = _repo({".github/workflows/ci.yml": (
        "on: [push]\njobs:\n  test:\n    steps:\n"
        "      - run: npm ci\n      - run: npm run test\n"
    )})
    assert detect_ci_test_command(repo, []) == "npm run test"


def test_first_matching_file_wins_sorted_order():
    repo = _repo({
        ".github/workflows/b_second.yml": "on: [push]\njobs:\n  t:\n    steps:\n      - run: cargo test\n",
        ".github/workflows/a_first.yml": "on: [push]\njobs:\n  t:\n    steps:\n      - run: pytest -q\n",
    })
    assert detect_ci_test_command(repo, []) == "pytest -q"   # a_first.yml sorts first


# ---- detect_contract(from_ci=...) ---------------------------------------------

def test_from_ci_prefers_ci_command_over_file_heuristic():
    # file heuristic alone would infer 'pytest -q' from pyproject.toml presence,
    # but the REAL CI command differs (e.g. runs with coverage) — from_ci must
    # prefer the ground-truth CI command, not the generic file guess.
    repo = _repo({
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "tests/test_a.py": "def test_x():\n    assert True\n",
        ".github/workflows/ci.yml": (
            "on: [push]\njobs:\n  test:\n    steps:\n"
            "      - run: pytest -q --cov=mypkg --cov-fail-under=90\n"
        ),
    })
    c, notes = detect_contract(repo, from_ci=True)
    hard = [v for v in c.verifiers if v.kind == VerifierKind.HARD]
    assert hard[0].command == "pytest -q --cov=mypkg --cov-fail-under=90"
    assert any("detected CI test command" in n for n in notes)


def test_from_ci_falls_back_to_heuristic_when_no_ci_match():
    repo = _repo({
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "tests/test_a.py": "def test_x():\n    assert True\n",
        # a workflow exists but has nothing recognizable as a test command
        ".github/workflows/ci.yml": "on: [push]\njobs:\n  lint:\n    steps:\n      - run: flake8\n",
    })
    c, notes = detect_contract(repo, from_ci=True)
    hard = [v for v in c.verifiers if v.kind == VerifierKind.HARD]
    assert hard[0].command == "pytest -q"   # fell back to the file heuristic
    assert not any("detected CI test command" in n for n in notes)


def test_from_ci_false_is_the_unchanged_default_behavior():
    repo = _repo({
        "pyproject.toml": "[tool.pytest.ini_options]\n",
        "tests/test_a.py": "def test_x():\n    assert True\n",
        ".github/workflows/ci.yml": (
            "on: [push]\njobs:\n  test:\n    steps:\n"
            "      - run: pytest -q --cov=mypkg\n"
        ),
    })
    c, _ = detect_contract(repo)   # from_ci defaults to False
    hard = [v for v in c.verifiers if v.kind == VerifierKind.HARD]
    assert hard[0].command == "pytest -q"   # the plain file-heuristic result


# ---- CLI end-to-end ------------------------------------------------------------

def test_cli_init_from_ci_end_to_end():
    runner = CliRunner()
    with runner.isolated_filesystem():
        os.makedirs(".github/workflows")
        with open(".github/workflows/ci.yml", "w") as f:
            f.write(
                "on: [push]\njobs:\n  test:\n    steps:\n"
                "      - run: pytest -q --maxfail=1\n"
            )
        r = runner.invoke(main, ["init", "--goal", "x", "--from-ci"])
        assert r.exit_code == 0, r.output
        assert "detected CI test command" in r.output

        import json
        data = json.load(open("loophole.json"))
        hard = [v for v in data["verifiers"] if v["kind"] == "hard"]
        assert hard[0]["command"] == "pytest -q --maxfail=1"

        rv = runner.invoke(main, ["contract", "validate", "loophole.json"])
        assert rv.exit_code == 0, rv.output
