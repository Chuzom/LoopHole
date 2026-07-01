"""Out-of-the-box swarm acceptance tests — the WHOLE run_goal pipeline end to end
(plan → schedule → execute in a worktree → merge gate → verifier → done), driven
DETERMINISTICALLY with no LLM: a fake planner + a shell `executor_command`.

These are the "install LoopHole and it just works" guarantees: the swarm accepts
correct work, REJECTS bad work, and the boundary blocks out-of-allowlist writes —
exactly as expected, with zero user configuration.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

from loophole.budget import Budget
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.loop import LoopConfig, Roles, run_goal
from loophole.provider import Completion, Provider
from loophole.state import Store


class _Planner(Provider):
    """Returns a fixed JSON task list — stands in for the planning LLM."""
    name = "fake-planner"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_e2e_")
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t",
                   "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                   "GIT_COMMITTER_EMAIL": "t@t"})
    return ws


_ADD_VERIFIER = ('/usr/bin/python3 -c "from add import add; assert add(2,3)==5; '
                 'assert add(0,0)==0; print(\'ok\')"')


def _run(executor_command, allowed_writes=None, verifier_cmd=_ADD_VERIFIER):
    ws = _git_ws()
    contract = GoalContract(
        goal="implement add(a,b) in add.py returning a+b",
        verifiers=[Verifier(kind=VerifierKind.HARD, command=verifier_cmd)],
        allowed_writes=allowed_writes or ["**"], max_rounds=4, timeout_seconds=120)
    store = Store(os.path.join(ws, ".loophole.db"))
    gid = store.create_goal(contract.to_json(), ws)
    planner = _Planner([{"id": "t1", "description": "create add.py with add(a,b)",
                         "depends_on": [], "reads": [], "writes": ["add.py"]}])
    roles = Roles(planner=planner, executor=planner, critic=planner)
    cfg = LoopConfig(max_parallel=1, skip_plan_critique=True,
                     executor_command=executor_command)
    outcome = run_goal(store, gid, contract, roles, Budget(), cfg)
    evs = store.events(gid)
    events = [e["kind"] for e in evs]
    why = "detail={!r} | reasons={}".format(outcome.detail, [
        (e["kind"], (e["payload"] or "")[:140]) for e in evs
        if e["kind"] in ("merge_gate_reject", "write_glob_violation",
                         "task_failed", "verify_run")])
    store.close()
    return outcome, events, ws, why


# correct + wrong implementations as shell one-liners (no LLM)
_WRITE_GOOD = r"printf 'def add(a, b):\n    return a + b\n' > add.py"
_WRITE_BAD = r"printf 'def add(a, b):\n    return a - b\n' > add.py"


def test_swarm_accepts_correct_work():
    outcome, events, _, why = _run(_WRITE_GOOD)
    assert outcome.status == "done", why            # verifier accepted it
    assert "verify_run" in events


def test_swarm_rejects_incorrect_work():
    outcome, _, ws, _ = _run(_WRITE_BAD)
    assert outcome.status != "done"                 # the gate refused buggy code
    # and the buggy file never reached a verified-green HEAD
    head_add = subprocess.run(["git", "-C", ws, "show", "HEAD:add.py"],
                              capture_output=True, text=True)
    assert "a - b" not in head_add.stdout            # not published


def test_boundary_blocks_out_of_allowlist_write():
    # agent writes add.py (fine) AND a file outside the allowlist -> boundary blocks
    cmd = _WRITE_GOOD + r" && printf 'leak' > secret.txt"
    outcome, events, _, _ = _run(cmd, allowed_writes=["add.py"])
    assert outcome.status != "done"                  # blocked, not silently accepted
    assert any(k in events for k in
               ("write_glob_violation", "merge_gate_reject")), events
