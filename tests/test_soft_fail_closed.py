"""Soft-judge fail-closed-to-human at the verdict layer.

A soft verifier that cannot be evaluated must not silently complete a goal. It
does not VETO a hard pass (passed stays True), but the verdict carries
``needs_human`` so the control loop escalates instead of declaring done.
"""
from __future__ import annotations

import subprocess
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.loop import verify_candidate


def _git_repo():
    ws = tempfile.mkdtemp(prefix="loophole_softfc_")
    integ = Integration(ws)  # inits git + initial commit
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--allow-empty"], cwd=ws)
    return integ


def test_abstained_soft_verifier_sets_needs_human_without_vetoing():
    integ = _git_repo()
    contract = GoalContract(
        goal="x",
        verifiers=[
            Verifier(kind=VerifierKind.HARD, command="true"),       # passes
            Verifier(kind=VerifierKind.SOFT, rubric="is it elegant?"),
        ],
    )
    # No soft judge => the rubric can't be evaluated.
    verdict = verify_candidate(contract, integ, baseline_total=None,
                               soft_judge=None)
    # Hard check passed and the abstention did NOT veto...
    assert verdict.passed is True
    # ...but completion must route through a human.
    assert verdict.needs_human, "abstained soft verifier must require human sign-off"


def test_clear_soft_pass_needs_no_human():
    from loophole.provider import Provider, Completion

    class OkJudge(Provider):
        name = "ok"
        def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
            return Completion(text='{"satisfied": true, "reasons": []}')

    integ = _git_repo()
    contract = GoalContract(
        goal="x",
        verifiers=[
            Verifier(kind=VerifierKind.HARD, command="true"),
            Verifier(kind=VerifierKind.SOFT, rubric="is it elegant?"),
        ],
    )
    verdict = verify_candidate(contract, integ, baseline_total=None,
                               soft_judge=OkJudge())
    assert verdict.passed is True
    assert not verdict.needs_human   # a clear verdict completes without a human
