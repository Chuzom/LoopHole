"""plan_critic fail-closed: an unparseable critic verdict rejects the plan.

Mirrors the soft-judge S11 fix — a malformed/garbled critic response must not be
read as approval.
"""
from __future__ import annotations

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.plan_critic import critique_plan, PlannedTask
from loophole.provider import Provider, Completion


class _Judge(Provider):
    name = "j"

    def __init__(self, text):
        self._text = text

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _plan():
    return [PlannedTask(id="t1", description="do x", depends_on=[],
                        reads=[], writes=["src/**"])]


def _contract():
    return GoalContract(goal="x", verifiers=[Verifier(kind=VerifierKind.HARD, command="true")])


def test_unparseable_verdict_rejects():
    crit = critique_plan(_Judge("the plan seems okay to me, ship it"), _contract(), _plan())
    assert crit.approved is False
    assert crit.issues


def test_explicit_approval_passes():
    crit = critique_plan(_Judge('{"approved": true, "issues": []}'), _contract(), _plan())
    assert crit.approved is True


def test_explicit_rejection_carries_issues():
    crit = critique_plan(
        _Judge('{"approved": false, "issues": ["t1 writes too broadly"]}'),
        _contract(), _plan())
    assert crit.approved is False
    assert "t1 writes too broadly" in crit.issues
