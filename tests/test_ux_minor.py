"""UX audit minor batch: init compaction, stats footer, superseded tasks."""

import json
import os

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.initializer import _compact_verifier, write_starter, load_contract
from loophole.scorecard import render_aggregate


def test_compact_verifier_drops_defaults_keeps_content():
    full = Verifier(kind=VerifierKind.HUMAN, prompt="ok?").to_dict()
    c = _compact_verifier(full)
    assert c == {"kind": "human", "prompt": "ok?"}     # nulls/empties gone
    hard = Verifier(kind=VerifierKind.HARD, command="pytest -q",
                    expected_test_delta=0).to_dict()
    c2 = _compact_verifier(hard)
    assert c2["command"] == "pytest -q"
    assert c2["expected_test_delta"] == 0              # non-default kept
    assert "rubric" not in c2


def test_scaffold_roundtrips_after_compaction(tmp_path):
    c = GoalContract.quick("x", verify_cmd="pytest -q")
    p = os.path.join(str(tmp_path), "loophole.json")
    write_starter(c, p)
    text = open(p).read()
    assert '"rubric": null' not in text               # compacted
    loaded = load_contract(p)                          # still valid
    assert loaded.hard_verifiers[0].command == "pytest -q"


def test_stats_footer_has_drilldown():
    out = render_aggregate({"runs": 3, "verified_done": 2, "failed_or_open": 1,
                            "total_cheats_blocked": 0, "total_rejections": 4,
                            "total_merges": 2}, color=False)
    assert "loophole runs" in out and "loophole audit" in out


def test_done_goal_supersedes_pending_tasks(tmp_path):
    import subprocess
    from loophole.budget import Budget
    from loophole.loop import LoopConfig, Roles, run_goal
    from loophole.provider import Completion, Provider
    from loophole.state import Store

    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(ws), check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=str(ws), check=True)
    store = Store(os.path.join(str(tmp_path), "s.db"))
    # a contract that's already satisfied: verifier passes immediately
    contract = GoalContract.quick("noop", verify_cmd="true")
    gid = store.create_goal(contract.to_json(), str(ws))
    # seed a leftover pending task that will never run
    store.add_task(gid, "leftover verify task", task_id=gid + "__leftover")

    class _NoopPlanner(Provider):
        name = "p"
        def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
            return Completion(text="[]")               # empty plan -> nothing to do

    roles = Roles(planner=_NoopPlanner(), executor=_NoopPlanner(), critic=_NoopPlanner())
    run_goal(store, gid, contract, roles, budget=Budget(),
             cfg=LoopConfig(skip_plan_critique=True))
    leftover = store.get_task(gid + "__leftover")
    if store.get_goal(gid)["status"] == "done":
        assert leftover.status == "superseded"
