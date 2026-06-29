from __future__ import annotations

import pytest

from loophole.contract import GoalContract, Verifier, VerifierKind, ContractError


def test_goal_with_no_verifier_rejected():
    c = GoalContract(goal="do something")
    with pytest.raises(ContractError) as e:
        c.validate()
    assert "no verifier" in str(e.value)


def test_soft_only_rejected():
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.SOFT, rubric="is it good?")])
    with pytest.raises(ContractError):
        c.validate()


def test_hard_verifier_ok():
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")])
    c.validate()  # no raise


def test_human_can_grant():
    c = GoalContract(goal="write an essay", verifiers=[
        Verifier(kind=VerifierKind.HUMAN, prompt="good?")])
    c.validate()


def test_hard_verifier_requires_command():
    with pytest.raises(ContractError):
        Verifier(kind=VerifierKind.HARD)


def test_roundtrip_json():
    c = GoalContract.quick("build x", verify_cmd="pytest -q")
    c2 = GoalContract.from_json(c.to_json())
    assert c2.goal == c.goal
    assert c2.hard_verifiers[0].command == "pytest -q"


def test_protected_paths_aggregation():
    c = GoalContract(goal="x", protected_paths=["tests/**"], verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest",
                 protected_paths=["conftest.py"], trusted_inputs=["spec.md"])])
    assert "tests/**" in c.all_protected_paths
    assert "conftest.py" in c.all_protected_paths
    assert "spec.md" in c.all_protected_paths
