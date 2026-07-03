"""#17 — interactive/`--goal` init, `init --ci`, and the executor adapter group."""
from __future__ import annotations

import json
import os

from click.testing import CliRunner

from loophole.cli import main
from loophole.initializer import CONTRACT_FILENAME


def test_init_goal_fills_in_the_goal():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["init", "--goal", "add a /health endpoint returning 200"])
        assert r.exit_code == 0, r.output
        data = json.load(open(CONTRACT_FILENAME))
        assert data["goal"] == "add a /health endpoint returning 200"
        assert "TODO" not in data["goal"]
        # bare `run` should now proceed past the TODO guard (it won't get far without
        # a model, but it must not be refused for a TODO goal)
        assert "run it:" in r.output


def test_init_ci_github_actions_writes_workflow():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["init", "--goal", "x", "--ci", "github-actions"])
        assert r.exit_code == 0, r.output
        wf = ".github/workflows/loophole-gate.yml"
        assert os.path.exists(wf)
        body = open(wf).read()
        # scaffolds the packaged Action (handles python/bubblewrap/install itself),
        # not the raw pip recipe
        assert "uses: Chuzom/loophole@v1" in body
        assert "contract: loophole.json" in body
        import yaml
        parsed = yaml.safe_load(body)
        assert parsed[True] == ["pull_request"]   # PyYAML 1.1 coerces bare `on:` -> True
        assert parsed["jobs"]["acceptance"]["steps"][-1]["uses"] == "Chuzom/loophole@v1"


def test_init_ci_gitlab_writes_workflow():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["init", "--goal", "x", "--ci", "gitlab"])
        assert r.exit_code == 0, r.output
        assert os.path.exists(".gitlab-ci.yml")
        assert "loophole-gate" in open(".gitlab-ci.yml").read()


def test_executor_list_and_test():
    runner = CliRunner()
    r = runner.invoke(main, ["executor", "list"])
    assert r.exit_code == 0
    assert "claude-code" in r.output and "--executor-command" in r.output

    # 'shell' adapter uses `sh`, which is always present -> ok
    r2 = runner.invoke(main, ["executor", "test", "shell"])
    assert r2.exit_code == 0 and "ok" in r2.output

    r3 = runner.invoke(main, ["executor", "test", "nope"])
    assert r3.exit_code != 0 and "unknown adapter" in r3.output


def test_init_pytest_repo_infers_verifier_and_scaffolds_action_workflow():
    """ROADMAP E1.4 acceptance: in a fresh pytest repo, `init` writes a contract
    whose verifier is the repo's real test command AND a workflow using the
    Action; `contract validate` passes on the result."""
    runner = CliRunner()
    with runner.isolated_filesystem():
        os.makedirs("tests")
        with open("tests/test_x.py", "w") as f:
            f.write("def test_ok():\n    assert True\n")

        r = runner.invoke(main, ["init", "--goal", "ship a passing test suite",
                                 "--ci", "github-actions"])
        assert r.exit_code == 0, r.output

        data = json.load(open(CONTRACT_FILENAME))
        hard = [v for v in data["verifiers"] if v["kind"] == "hard"]
        assert hard and hard[0]["command"] == "pytest -q"
        assert hard[0]["expected_test_delta"] == 0

        wf_body = open(".github/workflows/loophole-gate.yml").read()
        assert "uses: Chuzom/loophole@v1" in wf_body

        rv = runner.invoke(main, ["contract", "validate", CONTRACT_FILENAME])
        assert rv.exit_code == 0, rv.output
        assert "valid" in rv.output
