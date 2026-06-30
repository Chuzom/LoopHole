"""STRAT-2 — failure-mode UX in the Residual-Risk Report."""
from __future__ import annotations

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.report import residual_risk_report
from loophole.verifier import VerifyVerdict, VerifierResult


def _contract():
    return GoalContract(goal="ship it", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")])


def test_failed_report_shows_reason_and_fail_cause():
    verdict = VerifyVerdict(
        passed=False,
        results=[VerifierResult(name="hard:pytest -q", passed=False,
                                failures=["FAILED tests/test_x.py::test_y"])],
        failures=["FAILED tests/test_x.py::test_y"])
    out = residual_risk_report(_contract(), verdict, "failed", 5, "n/a",
                               detail="step budget exhausted")
    assert "FAILED" in out
    assert "Reason: step budget exhausted" in out          # precise stop reason
    assert "[FAIL] hard:pytest -q" in out
    assert "tests/test_x.py::test_y" in out                # WHY it failed
    assert "NEXT STEPS" in out and "resume" in out


def test_abstained_soft_check_is_marked_and_flagged_for_human():
    verdict = VerifyVerdict(
        passed=True,
        results=[
            VerifierResult(name="hard:pytest -q", passed=True, metrics={"passed": 3.0}),
            VerifierResult(name="soft:is it elegant?", passed=True, abstained=True),
        ],
        needs_human=["soft:is it elegant?: no soft judge configured"])
    out = residual_risk_report(_contract(), verdict, "paused", 2, "n/a",
                               detail="soft verifier indeterminate")
    assert "[ABSTAIN] soft:is it elegant?" in out          # not shown as PASS
    assert "Needs HUMAN sign-off" in out
    assert "no soft judge configured" in out
    assert "NEXT STEPS" in out


def test_done_report_has_no_reason_or_next_steps():
    verdict = VerifyVerdict(passed=True, results=[
        VerifierResult(name="hard:pytest -q", passed=True, metrics={"passed": 3.0})])
    out = residual_risk_report(_contract(), verdict, "done", 1, "n/a", detail="all verifiers passed")
    assert "Reason:" not in out          # detail suppressed on success
    assert "NEXT STEPS" not in out
    assert "[PASS] hard:pytest -q" in out
