"""GAP 1 regression: the boundary must compare against the ORIGINAL base commit,
not current HEAD. If it used HEAD, tampering already committed would be invisible.
"""
from __future__ import annotations

import os
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.loop import verify_candidate


def _write(root, rel, text):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p) or root, exist_ok=True)
    with open(p, "w") as f:
        f.write(text)


def test_boundary_catches_tampering_against_original_base():
    ws = tempfile.mkdtemp(prefix="loophole_test_ws_")
    # original protected file
    _write(ws, "tests/test_contract_check.py", "def test_real():\n    assert True\n")
    integ = Integration(ws)  # inits git, commits initial state
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "add protected test"], cwd=ws)
    base_commit = integ.head()

    # Now an agent TAMPERS with the protected file and commits it into HEAD
    _write(ws, "tests/test_contract_check.py", "def test_real():\n    assert True  # gutted\n")
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "weaken test"], cwd=ws)

    contract = GoalContract(
        goal="x",
        protected_paths=["tests/**"],
        verifiers=[Verifier(kind=VerifierKind.HARD, command="true")],
    )

    # With the correct base_commit, the boundary must FLAG the tampering...
    verdict = verify_candidate(contract, integ, baseline_total=None,
                               base_commit=base_commit)
    assert verdict.boundary_violations, "tampering against original base must be detected"
    assert not verdict.passed

    # ...whereas using HEAD as the baseline (the old bug) would miss it.
    verdict_buggy = verify_candidate(contract, integ, baseline_total=None,
                                     base_commit=integ.head())
    assert not verdict_buggy.boundary_violations, "HEAD-vs-HEAD cannot see the change"
