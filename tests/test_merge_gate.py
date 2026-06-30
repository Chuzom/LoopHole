"""R3 — per-merge re-verification gate.

A merge is re-verified BEFORE it is published to HEAD; if it verifies red it is
rolled back, so a bad merge never poisons the worktrees that branch off HEAD.
These tests exercise the building blocks directly (gate_only verification +
reset_hard rollback) without standing up the full provider/planner stack.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.loop import verify_candidate


def _repo_with(content=None):
    ws = tempfile.mkdtemp(prefix="loophole_gate_")
    integ = Integration(ws)
    if content is not None:
        with open(os.path.join(ws, "good.txt"), "w") as f:
            f.write(content)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--allow-empty"], cwd=ws)
    return integ, ws


def test_gate_only_skips_the_soft_judge():
    integ, _ = _repo_with("ok")
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true"),
        Verifier(kind=VerifierKind.SOFT, rubric="is it elegant?"),
    ])
    full = verify_candidate(contract, integ, None, soft_judge=None, gate_only=False)
    gate = verify_candidate(contract, integ, None, soft_judge=None, gate_only=True)
    # full run abstains the soft verifier -> needs human; the gate ignores soft.
    assert full.needs_human and not gate.needs_human
    assert gate.passed is True


def test_reset_hard_rolls_back_head():
    integ, ws = _repo_with("v1")
    head1 = integ.head()
    with open(os.path.join(ws, "good.txt"), "w") as f:
        f.write("v2")
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "v2"], cwd=ws)
    assert integ.head() != head1
    integ.reset_hard(head1)
    assert integ.head() == head1
    with open(os.path.join(ws, "good.txt")) as f:
        assert f.read() == "v1"


def test_gate_rejects_red_merge_and_rollback_restores_green():
    # hard verifier requires good.txt to exist in the candidate checkout
    integ, ws = _repo_with("ok")
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="test -f good.txt"),
    ])
    green = integ.head()
    assert verify_candidate(contract, integ, None, base_commit=green,
                            gate_only=True).passed

    # simulate a merge that breaks HEAD (deletes the required file)
    os.remove(os.path.join(ws, "good.txt"))
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "break"], cwd=ws)
    assert not verify_candidate(contract, integ, None, base_commit=green,
                                gate_only=True).passed

    # the gate's response: roll HEAD back -> verified-green again
    integ.reset_hard(green)
    assert verify_candidate(contract, integ, None, base_commit=green,
                            gate_only=True).passed
