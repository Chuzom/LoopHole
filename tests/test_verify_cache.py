"""ARCH-2 — deterministic verification is memoised by tree sha.

The per-merge gate and the round-level verify both run on the same HEAD; the
hard verifiers (and boundary/test-count) depend only on the tree, so they should
run once per unique tree, not once per call. These tests count underlying
verifier invocations to prove the dedup — and prove the cache stores only the
hard results (no soft leakage).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from unittest import mock

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.verifier import VerifierResult
import loophole.loop as loop_mod
from loophole.loop import verify_candidate


def _repo():
    ws = tempfile.mkdtemp(prefix="loophole_cache_")
    integ = Integration(ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--allow-empty"], cwd=ws)
    return integ, ws


def test_deterministic_checks_cached_by_tree():
    integ, ws = _repo()
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")])
    calls = {"n": 0}

    def fake(v, cwd, timeout=600):
        calls["n"] += 1
        return VerifierResult(name="hard:true", passed=True,
                              metrics={"passed": 1.0, "total": 1.0})

    with mock.patch.object(loop_mod, "run_command_verifier", side_effect=fake):
        g = verify_candidate(contract, integ, None, gate_only=True)
        assert g.passed and calls["n"] == 1
        # same tree -> the round-level verify reuses the gate's hard result
        f = verify_candidate(contract, integ, None, gate_only=False)
        assert f.passed and calls["n"] == 1
        # tree changes -> recompute
        with open(os.path.join(ws, "new.txt"), "w") as fh:
            fh.write("z")
        subprocess.run(["git", "add", "-A"], cwd=ws)
        subprocess.run(["git", "commit", "-q", "-m", "loophole task: x"], cwd=ws)
        g2 = verify_candidate(contract, integ, None, gate_only=True)
        assert g2.passed and calls["n"] == 2


def test_cache_does_not_leak_soft_results_into_hard():
    integ, _ = _repo()
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true"),
        Verifier(kind=VerifierKind.SOFT, rubric="is it elegant?"),
    ])
    calls = {"n": 0}

    def fake(v, cwd, timeout=600):
        calls["n"] += 1
        return VerifierResult(name="hard:true", passed=True)

    with mock.patch.object(loop_mod, "run_command_verifier", side_effect=fake):
        full = verify_candidate(contract, integ, None, soft_judge=None, gate_only=False)
        assert full.needs_human            # soft abstained (no judge)
        gate = verify_candidate(contract, integ, None, gate_only=True)
        assert calls["n"] == 1             # hard verifier reused from cache
        # the gate result must carry only the hard verifier, never the soft entry
        assert all("soft:" not in r.name for r in gate.results)
        assert not gate.needs_human


def test_persistent_cache_survives_resume():
    """ARCH-5: the verify cache is store-backed, so a fresh Integration (resume)
    still hits — the hard verifier runs once across the 'restart'."""
    import os
    from loophole.state import Store
    integ, ws = _repo()
    store = Store(os.path.join(ws, "s.db"))
    contract = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")])
    calls = {"n": 0}

    def fake(v, cwd, timeout=600):
        calls["n"] += 1
        return VerifierResult(name="hard:true", passed=True, metrics={"passed": 1.0})

    with mock.patch.object(loop_mod, "run_command_verifier", side_effect=fake):
        g1 = verify_candidate(contract, integ, None, gate_only=True, store=store)
        assert g1.passed and calls["n"] == 1
        # simulate resume: a brand-new Integration (cold in-memory cache), same store
        integ2 = Integration(ws)
        g2 = verify_candidate(contract, integ2, None, gate_only=True, store=store)
        assert g2.passed and calls["n"] == 1   # served from the persistent cache
    store.close()
