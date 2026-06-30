"""The "can't-fake-done" demo — packaged so `loophole demo` works for any install.

The wedge in one command, LLM-free and deterministic: a naive agent declares
success on buggy code; loophole runs the contract's hard verifier and REFUSES;
after the bug is actually fixed, the verifier passes and loophole accepts.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time

from .contract import GoalContract, Verifier, VerifierKind
from .integration import Integration
from .loop import verify_candidate
from .report import residual_risk_report

BUGGY = "def add(a, b):\n    return a - b  # <-- bug: should be a + b\n"
FIXED = "def add(a, b):\n    return a + b\n"
VERIFY = ('python3 -c "from add import add; '
          'assert add(2, 3) == 5; assert add(-1, 1) == 0; print(\'VERIFIED\')"')
GOAL = "implement add(a, b) in add.py so that add(a, b) == a + b"


def _repo(impl: str):
    d = tempfile.mkdtemp(prefix="loophole_demo_")
    with open(os.path.join(d, "add.py"), "w") as f:
        f.write(impl)
    integ = Integration(d)
    subprocess.run(["git", "add", "-A"], cwd=d)
    subprocess.run(["git", "commit", "-q", "-m", "loophole: demo", "--allow-empty"], cwd=d)
    return integ, d


def _commit(d: str, impl: str):
    with open(os.path.join(d, "add.py"), "w") as f:
        f.write(impl)
    subprocess.run(["git", "add", "-A"], cwd=d)
    subprocess.run(["git", "commit", "-q", "-m", "loophole task: fix add"], cwd=d)


def run_demo(verbose: bool = True, pause: float = 0.0):
    """Returns (loophole_accepted_buggy, loophole_accepted_fixed) = (False, True).

    ``pause`` adds a dramatic delay between acts (used by `loophole demo --slow`).
    """
    def out(*a):
        if verbose:
            print(*a)

    def beat():
        if pause and verbose:
            time.sleep(pause)

    contract = GoalContract(goal=GOAL, verifiers=[
        Verifier(kind=VerifierKind.HARD, command=VERIFY)])
    integ, d = _repo(BUGGY)

    out("=" * 60)
    out("  loophole — the 'can't-fake-done' demo")
    out("=" * 60)
    out("\nGOAL: " + GOAL)
    out("(the implementation has a bug: it returns a - b)\n")
    beat()
    out("-" * 60)
    out("[1] A naive agent simply declares success:")
    out("    \U0001F916  \"Done! I've implemented add(). ✅\"")
    out("    ...but the acceptance check actually FAILS on this code.\n")
    beat()
    out("[2] loophole refuses to take the agent's word — it runs the")
    out("    contract's hard verifier against a fresh checkout:\n")
    buggy = verify_candidate(contract, integ, baseline_total=None)
    status = "done" if buggy.passed else "failed"
    out(residual_risk_report(contract, buggy, status, 1, "demo (no model)",
                             detail="hard verifier did not pass"))
    out("\n    → loophole does NOT declare done. The false claim is caught.\n")
    beat()
    out("-" * 60)
    out("[3] Now we actually fix the bug (return a + b) and re-verify:\n")
    _commit(d, FIXED)
    fixed = verify_candidate(contract, integ, baseline_total=None)
    out(residual_risk_report(contract, fixed, "done" if fixed.passed else "failed",
                             2, "demo (no model)", detail="all verifiers passed"))
    out("\n    → Only now does loophole accept completion.\n")
    out("=" * 60)
    out("Takeaway: 'done' means the verifier passed — not that an LLM said so.")
    out("=" * 60)
    return buggy.passed, fixed.passed


if __name__ == "__main__":
    run_demo(verbose=True)
