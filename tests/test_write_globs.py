"""S5 — the write allowlist is ENFORCED at commit, not merely advertised.

Unit tests cover the policy (GoalContract.write_violations); an integration test
drives a real git worktree through stage_changes to prove an out-of-bounds file
is caught before it can be committed/merged.
"""
from __future__ import annotations

import subprocess
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration


def _c(allowed=None, protected=None):
    return GoalContract(
        goal="x",
        verifiers=[Verifier(kind=VerifierKind.HARD, command="true")],
        allowed_writes=allowed if allowed is not None else ["**"],
        protected_paths=protected or [],
    )


# ---- policy ----------------------------------------------------------------

def test_default_allows_everything():
    assert _c().write_violations(["src/a.py", "anything/b.txt"]) == []


def test_out_of_glob_is_flagged():
    v = _c(allowed=["src/**"]).write_violations(["src/a.py", "secrets/keys.txt"])
    assert len(v) == 1 and "secrets/keys.txt" in v[0]


def test_task_writes_narrow_the_contract():
    # contract permits all, but the task only declared src/** -> infra/ is a violation
    c = _c(allowed=["**"])
    v = c.write_violations(["src/a.py", "infra/deploy.sh"], task_writes=["src/**"])
    assert len(v) == 1 and "infra/deploy.sh" in v[0]


def test_protected_path_blocked_even_if_in_allowlist():
    c = _c(allowed=["**"], protected=["tests/**"])
    v = c.write_violations(["tests/test_x.py"])
    assert len(v) == 1 and "protected" in v[0]


def test_empty_task_writes_falls_back_to_contract():
    c = _c(allowed=["src/**"])
    assert c.write_violations(["src/a.py"], task_writes=[]) == []
    assert c.write_violations(["x.py"], task_writes=[]) != []


# ---- enforcement through a real worktree -----------------------------------

def _repo():
    ws = tempfile.mkdtemp(prefix="loophole_wg_")
    integ = Integration(ws)
    subprocess.run(["git", "add", "-A"], cwd=ws)
    subprocess.run(["git", "commit", "-q", "-m", "init", "--allow-empty"], cwd=ws)
    return integ


def test_stage_changes_lists_out_of_bounds_file():
    integ = _repo()
    wt = integ.make_worktree("t1", base_commit=integ.head())
    # an agent writes both an in-bounds and an out-of-bounds file
    import os
    os.makedirs(os.path.join(wt, "src"), exist_ok=True)
    with open(os.path.join(wt, "src", "ok.py"), "w") as f:
        f.write("x = 1\n")
    with open(os.path.join(wt, "escape.sh"), "w") as f:
        f.write("rm -rf /\n")
    changed = integ.stage_changes("t1")
    assert set(changed) == {"src/ok.py", "escape.sh"}
    # the contract that only permits src/** must reject the escape
    c = _c(allowed=["src/**"])
    v = c.write_violations(changed)
    assert len(v) == 1 and "escape.sh" in v[0]
    integ.discard_worktree("t1")
