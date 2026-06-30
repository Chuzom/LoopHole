"""Value scorecard — per-run + aggregate quantified feedback."""
from __future__ import annotations

import os, tempfile
from loophole.state import Store
from loophole.scorecard import run_scorecard, render_scorecard, aggregate, render_aggregate
from loophole.contract import GoalContract, Verifier, VerifierKind


def _store():
    d = tempfile.mkdtemp(prefix="loophole_sc_")
    return Store(os.path.join(d, "s.db"))


def _cjson(goal="g"):
    return GoalContract(goal=goal, verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")]).to_json()


def test_run_scorecard_counts():
    s = _store(); gid = s.create_goal(_cjson(), "/ws")
    s.add_task(gid, "a", task_id="t1"); s.set_task_status("t1", "done", gid)
    s.add_task(gid, "b", task_id="t2"); s.set_task_status("t2", "failed", gid)
    s.log("verify_run", goal_id=gid, payload={"passed": False, "score": 4})
    s.log("merge_gate_reject", goal_id=gid, payload={"failures": ["x"]})
    s.log("write_glob_violation", goal_id=gid, payload={"violations": ["y"]})
    s.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
    s.set_goal_status(gid, "done")
    sc = run_scorecard(s, gid)
    assert sc["verified_done"] is True
    assert sc["rounds"] == 2 and sc["verifier_rejections"] == 1
    assert sc["cheats_blocked"] == 2          # merge_gate_reject + write_glob_violation
    assert sc["tasks_merged"] == 1 and sc["tasks_failed"] == 1
    txt = render_scorecard(sc, color=False)
    assert "VERIFIED DONE" in txt and "cheat" in txt
    s.close()


def test_aggregate_across_runs():
    s = _store()
    g1 = s.create_goal(_cjson("one"), "/w"); s.log("write_glob_violation", goal_id=g1, payload={}); s.set_goal_status(g1, "done")
    g2 = s.create_goal(_cjson("two"), "/w"); s.set_goal_status(g2, "failed")
    agg = aggregate(s)
    assert agg["runs"] == 2 and agg["verified_done"] == 1 and agg["failed_or_open"] == 1
    assert agg["total_cheats_blocked"] == 1
    assert "verifier-backed" in render_aggregate(agg, color=False)
    s.close()


def test_empty_stats_message():
    assert "No runs yet" in render_aggregate(aggregate(_store()), color=False)
