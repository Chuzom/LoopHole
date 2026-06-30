from __future__ import annotations

from loophole.contract import Verifier, VerifierKind
from loophole.verifier import evaluate_soft_verifier
from loophole.provider import Provider, Completion, Msg


class FakeJudge(Provider):
    name = "fake"

    def __init__(self, satisfied: bool):
        self._satisfied = satisfied

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        body = '{"satisfied": %s, "reasons": ["because"]}' % (
            "true" if self._satisfied else "false")
        return Completion(text=body)


class GarbageJudge(Provider):
    name = "garbage"

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text="I think it's probably fine, no JSON here")


def test_soft_verifier_abstains_without_judge(tmp_path):
    # No judge => cannot evaluate the rubric. Fail-closed-to-human: don't veto,
    # but mark abstained so the loop escalates instead of silently completing.
    v = Verifier(kind=VerifierKind.SOFT, rubric="is it good?")
    r = evaluate_soft_verifier(v, str(tmp_path), judge=None)
    assert r.passed is True
    assert r.abstained is True


def test_soft_verifier_passes_when_satisfied(tmp_path):
    (tmp_path / "x.py").write_text("print('hi')")
    v = Verifier(kind=VerifierKind.SOFT, rubric="prints something")
    r = evaluate_soft_verifier(v, str(tmp_path), judge=FakeJudge(True))
    assert r.passed is True
    assert r.abstained is False   # a clear verdict is NOT an abstention


def test_soft_verifier_vetoes_when_unsatisfied(tmp_path):
    (tmp_path / "x.py").write_text("pass")
    v = Verifier(kind=VerifierKind.SOFT, rubric="must print something")
    r = evaluate_soft_verifier(v, str(tmp_path), judge=FakeJudge(False))
    assert r.passed is False
    assert r.abstained is False
    assert r.failures


def test_soft_verifier_abstains_on_judge_error(tmp_path):
    class Boom(Provider):
        name = "boom"
        def complete(self, *a, **k):
            raise RuntimeError("model down")

    v = Verifier(kind=VerifierKind.SOFT, rubric="x")
    r = evaluate_soft_verifier(v, str(tmp_path), judge=Boom())
    assert r.passed is True        # infra failure must not veto a hard pass...
    assert r.abstained is True     # ...but it must escalate to a human


def test_soft_verifier_abstains_on_unparseable_verdict(tmp_path):
    v = Verifier(kind=VerifierKind.SOFT, rubric="x")
    r = evaluate_soft_verifier(v, str(tmp_path), judge=GarbageJudge())
    assert r.passed is True
    assert r.abstained is True     # no clear verdict => human, not fail-open
