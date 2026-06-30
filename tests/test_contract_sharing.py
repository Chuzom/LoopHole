"""VIS-3 — shareable contracts: templates, URL loading, contract CLI."""
from __future__ import annotations

import json
import os
import pathlib
import tempfile

import pytest
from click.testing import CliRunner

from loophole.cli import main
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.initializer import (list_templates, load_template_raw, load_contract,
                                  CONTRACT_FILENAME)


# ---- templates -------------------------------------------------------------

def test_bundled_templates_present_and_valid():
    names = list_templates()
    assert {"python-lib", "refactor-frozen-tests", "human-reviewed"} <= set(names)
    for n in names:
        GoalContract.from_json(load_template_raw(n))   # parses + shape-valid


def test_unknown_template_raises_with_choices():
    with pytest.raises(ValueError) as e:
        load_template_raw("does-not-exist")
    assert "python-lib" in str(e.value)


# ---- URL loading (acceptance-spec-as-code is shareable) --------------------

def test_load_contract_from_file_url(tmp_path):
    raw = GoalContract(goal="remote goal", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json()
    p = tmp_path / "shared.json"
    p.write_text(raw)
    c = load_contract(p.as_uri())          # file:// URL exercises the URL branch
    assert c.goal == "remote goal"


# ---- CLI -------------------------------------------------------------------

def test_cli_init_from_template():
    runner = CliRunner()
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["init", "--template", "refactor-frozen-tests"])
        assert r.exit_code == 0, r.output
        data = json.load(open(CONTRACT_FILENAME))
        # the frozen-tests template protects tests/**
        assert "tests/**" in data["protected_paths"]

        # unknown template fails cleanly with the available list
        r2 = runner.invoke(main, ["init", "--template", "nope", "--force"])
        assert r2.exit_code != 0 and "python-lib" in r2.output


def test_cli_list_templates():
    r = CliRunner().invoke(main, ["init", "--list-templates"])
    assert r.exit_code == 0 and "human-reviewed" in r.output


def test_cli_contract_validate_and_show(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(GoalContract(goal="g", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json())
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"goal": "no verifiers", "verifiers": []}))

    runner = CliRunner()
    r = runner.invoke(main, ["contract", "validate", str(good)])
    assert r.exit_code == 0 and "valid" in r.output

    r2 = runner.invoke(main, ["contract", "validate", str(bad)])
    assert r2.exit_code != 0          # no verifier => can't define done

    r3 = runner.invoke(main, ["contract", "show", str(good)])
    assert r3.exit_code == 0 and "pytest -q" in r3.output
