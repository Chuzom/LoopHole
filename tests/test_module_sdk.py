"""Module SDK — a toy module proves the seam: register a graded verifier kind,
have it run at round level (never in the gate/cache), and keep the code firewall."""
from __future__ import annotations

import os
import subprocess
import tempfile

import pytest

from loophole import plugins
from loophole.contract import GoalContract, Verifier, VerifierKind, ContractError
from loophole.integration import Integration
from loophole.loop import verify_candidate


# A stand-in for what a finance/strategy module would register.
def _stat_eval(verifier, cand_dir, ctx):
    ctx.log("statistical_trial", {"verifier": verifier.kind_str})
    pr = verifier.params.get("pass_rate", 0.8)
    return plugins.ModuleResult(
        name="stat:" + verifier.kind_str,
        passed=pr >= verifier.params.get("min", 0.95),
        confidence=pr,
        residual_risk=["future regime breaks are unverifiable"],
        evidence={"pass_rate": pr},
        detail="deflated-Sharpe proxy pass_rate={}".format(pr))


@pytest.fixture(autouse=True)
def _register():
    plugins.register_verifier("test-stat", _stat_eval)
    yield
    plugins._EVALUATORS.pop("test-stat", None)


def _repo():
    ws = tempfile.mkdtemp(prefix="loophole_mod_")
    integ = Integration(ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--allow-empty"], cwd=ws)
    return integ


def _contract(pass_rate, min_=0.95):
    return GoalContract(goal="quant goal", domain="quant", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true"),
        Verifier(kind="test-stat", params={"pass_rate": pass_rate, "min": min_})])


def test_module_kind_is_not_builtin_and_excluded_from_hard():
    v = Verifier(kind="test-stat", params={"x": 1})
    assert not v.is_builtin and v.kind_str == "test-stat"
    c = _contract(0.97)
    assert c.hard_verifiers and all(hv.is_builtin for hv in c.hard_verifiers)
    # round-trips through json
    c2 = GoalContract.from_json(c.to_json())
    mv = [x for x in c2.verifiers if not x.is_builtin][0]
    assert mv.kind_str == "test-stat" and mv.params["pass_rate"] == 0.97


def test_module_verifier_grades_at_round_level():
    integ = _repo()
    v = verify_candidate(_contract(0.97), integ, None, gate_only=False)
    assert v.passed is True                       # hard true + module passed
    assert abs(v.confidence - 0.97) < 1e-9        # graded, not 1.0
    assert any("regime breaks" in r for r in v.residual_risk)


def test_module_verifier_can_veto():
    integ = _repo()
    v = verify_candidate(_contract(0.50), integ, None, gate_only=False)
    assert v.passed is False                      # module result below threshold vetoes
    assert v.confidence == 0.50


def test_module_verifier_never_runs_in_the_gate():
    integ = _repo()
    # gate_only is the per-merge deterministic gate: module verifiers must be skipped,
    # confidence stays a clean 1.0, and only the hard check decides passed.
    v = verify_candidate(_contract(0.10), integ, None, gate_only=True)
    assert v.passed is True and v.confidence == 1.0


def test_code_domain_firewall_rejects_module_kinds():
    c = GoalContract(goal="x", domain="code", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true"),
        Verifier(kind="test-stat", params={})])
    with pytest.raises(ContractError):
        c.validate()
    # the same verifiers are allowed under a non-code domain
    GoalContract(goal="x", domain="quant", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true"),
        Verifier(kind="test-stat", params={})]).validate()
