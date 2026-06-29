from __future__ import annotations

import os
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.verifier import (parse_pytest, check_boundary, check_test_count,
                               VerifyVerdict, VerifierResult)


def test_parse_pytest_summary():
    out = "=== 3 failed, 5 passed, 1 skipped in 0.2s ==="
    m = parse_pytest(out)
    assert m["failed"] == 3 and m["passed"] == 5 and m["skipped"] == 1
    assert m["total"] == 9


def test_parse_pytest_all_pass():
    m = parse_pytest("==== 12 passed in 1.1s ====")
    assert m["passed"] == 12 and m["failed"] == 0


def test_check_test_count_detects_drop():
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest", expected_test_delta=0)])
    violations = check_test_count(c, {"total": 8}, baseline_total=10)
    assert violations and "dropped" in violations[0]


def test_check_test_count_ok_when_grown():
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest", expected_test_delta=0)])
    assert check_test_count(c, {"total": 12}, baseline_total=10) == []


def test_boundary_detects_modified_protected_file():
    base = tempfile.mkdtemp()
    cand = tempfile.mkdtemp()
    with open(os.path.join(base, "conftest.py"), "w") as f:
        f.write("ORIGINAL")
    with open(os.path.join(cand, "conftest.py"), "w") as f:
        f.write("HACKED")
    c = GoalContract(goal="x", protected_paths=["conftest.py"], verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest")])
    violations = check_boundary(c, base, cand)
    assert violations and "conftest.py" in violations[0]


def test_verdict_metric_score_rewards_passes():
    v_pass = VerifyVerdict(passed=True, results=[
        VerifierResult("hard:pytest", True, {"passed": 5, "failed": 0})])
    v_fail = VerifyVerdict(passed=False, results=[
        VerifierResult("hard:pytest", False, {"passed": 2, "failed": 3})])
    assert v_pass.metric_score > v_fail.metric_score


def test_boundary_violation_tanks_score():
    v = VerifyVerdict(passed=False, results=[
        VerifierResult("hard:pytest", True, {"passed": 5})],
        boundary_violations=["protected path modified: conftest.py"])
    assert v.metric_score < 0
