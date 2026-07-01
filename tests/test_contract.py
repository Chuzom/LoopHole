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


def test_auto_protect_contract_file_and_verifier_scripts(tmp_path):
    (tmp_path / "loophole.json").write_text("{}")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "check.py").write_text("print('ok')")
    c = GoalContract.quick("build x", verify_cmd="python3 tests/check.py --strict")
    added = c.auto_protect(str(tmp_path))
    assert added == ["loophole.json", "tests/check.py"]
    # non-file tokens (python3, --strict) are never protected
    assert "python3" not in c.all_protected_paths
    # a second call is a no-op (idempotent)
    assert c.auto_protect(str(tmp_path)) == []
    # and the boundary now actually blocks edits to the goalpost
    violations = c.write_violations(["tests/check.py", "src/app.py"])
    assert any("tests/check.py" in v and "protected" in v for v in violations)
    assert not any("src/app.py" in v for v in violations)


def test_auto_protect_skips_absent_absolute_and_escaping_paths(tmp_path):
    c = GoalContract.quick("build x",
                           verify_cmd="/usr/bin/true ../outside.py missing.py")
    assert c.auto_protect(str(tmp_path)) == []
    assert c.all_protected_paths == []
