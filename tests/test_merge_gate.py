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


def _commit(ws, subject, fname="f.txt", content="x"):
    with open(os.path.join(ws, fname), "w") as f:
        f.write(content)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", subject], cwd=ws)


# ---- ARCH-1: startup crash reconciliation -----------------------------------

def test_reconcile_rolls_back_ungated_loophole_merge():
    integ, ws = _repo_with("ok")
    green = integ.head()
    integ.mark_green(green)
    # simulate a crash that left an ungated loophole merge above green
    _commit(ws, "loophole task: add feature", "feat.txt", "1")
    assert integ.head() != green
    rolled = integ.reconcile_head()
    assert rolled == green
    assert integ.head() == green
    assert not os.path.exists(os.path.join(ws, "feat.txt"))


def test_reconcile_preserves_a_user_commit():
    integ, ws = _repo_with("ok")
    green = integ.head()
    integ.mark_green(green)
    # a human committed on top of green — must NEVER be rolled back
    _commit(ws, "fix: my manual hotfix", "hotfix.txt", "1")
    user_head = integ.head()
    assert integ.reconcile_head() is None
    assert integ.head() == user_head
    assert os.path.exists(os.path.join(ws, "hotfix.txt"))


def test_reconcile_noop_when_head_is_green():
    integ, _ = _repo_with("ok")
    integ.mark_green(integ.head())
    assert integ.reconcile_head() is None


def test_reconcile_refuses_when_user_commit_sits_between():
    integ, ws = _repo_with("ok")
    green = integ.head()
    integ.mark_green(green)
    _commit(ws, "loophole task: step 1", "a.txt", "1")
    _commit(ws, "chore: user tweak", "b.txt", "1")   # user commit above a loophole one
    head = integ.head()
    assert integ.reconcile_head() is None   # mixed history -> hands off entirely
    assert integ.head() == head


# ---- ARCH-4: stage-on-side-ref (verify off-HEAD, publish only when green) ----

def _branch_with_change(integ, tid, fname, content="x\n"):
    wt = integ.make_worktree(tid, base_commit=integ.head())
    with open(os.path.join(wt, fname), "w") as f:
        f.write(content)
    integ.commit_worktree(tid, "add " + fname)


def test_stage_merge_does_not_move_head():
    integ, ws = _repo_with("base")
    base = integ.head()
    _branch_with_change(integ, "t1", "feature.txt")
    candidate = integ.stage_merge("t1", base)
    assert candidate and candidate != base
    assert integ.head() == base                     # side-ref staging left HEAD put
    integ.discard_worktree("t1")


def test_stage_verify_publish_happy_path():
    integ, ws = _repo_with("base")
    base = integ.head()
    _branch_with_change(integ, "t1", "feature.txt")
    candidate = integ.stage_merge("t1", base)
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="test -f feature.txt")])
    v = verify_candidate(contract, integ, None, base_commit=base, gate_only=True,
                         pinned_commit=candidate)
    assert v.passed                                  # verified the OFF-HEAD candidate
    assert integ.try_publish(candidate, base) is True
    assert integ.head() == candidate
    assert os.path.exists(os.path.join(ws, "feature.txt"))   # working tree updated
    integ.discard_worktree("t1")


def test_gate_failure_never_moves_head():
    integ, ws = _repo_with("base")
    base = integ.head()
    _branch_with_change(integ, "t2", "x.txt")
    candidate = integ.stage_merge("t2", base)
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="test -f does_not_exist")])
    v = verify_candidate(contract, integ, None, base_commit=base, gate_only=True,
                         pinned_commit=candidate)
    assert not v.passed
    assert integ.head() == base       # R3 invariant: a red gate never advanced HEAD
    integ.discard_worktree("t2")


def test_try_publish_refuses_when_head_moved():
    integ, ws = _repo_with("base")
    base = integ.head()
    _branch_with_change(integ, "t3", "a.txt")
    candidate = integ.stage_merge("t3", base)
    _commit(ws, "loophole task: other", "other.txt", "1")   # HEAD races ahead
    moved = integ.head()
    assert integ.try_publish(candidate, base) is False       # base no longer HEAD
    assert integ.head() == moved                             # publish refused cleanly
    integ.discard_worktree("t3")
