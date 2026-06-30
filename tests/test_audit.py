"""VIS-2 — audit-trail rendering + CLI."""
from __future__ import annotations

import os
import tempfile

from click.testing import CliRunner

from loophole.audit import render_audit, render_runs
from loophole.cli import main
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.state import Store


def _store():
    d = tempfile.mkdtemp(prefix="loophole_audit_")
    return Store(os.path.join(d, "s.db")), d


def _contract_json():
    return GoalContract(goal="ship the thing", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json()


def _seed(store):
    gid = store.create_goal(_contract_json(), "/tmp/ws")
    store.log("task_done", goal_id=gid, task_id="t1", payload={"summary": "wrote add.py"})
    store.log("write_glob_violation", goal_id=gid, task_id="t2",
              payload={"violations": ["infra/deploy.sh is outside allowed writes"]})
    store.log("merge_gate_reject", goal_id=gid, task_id="t3",
              payload={"failures": ["FAILED tests/test_x.py"]})
    store.log("verify_run", goal_id=gid, payload={"passed": False, "score": -2})
    store.log("merge_gate_reconcile", goal_id=gid, payload={"reset_to": "abc123def456"})
    store.set_goal_status(gid, "paused")
    return gid


def test_render_audit_surfaces_boundary_decisions():
    store, _ = _store()
    gid = _seed(store)
    g = store.get_goal(gid)
    out = render_audit(g, store.events(gid), "ship the thing")
    assert "Audit Trail" in out
    assert "ship the thing" in out and gid in out
    assert "write-allowlist VIOLATION" in out
    assert "merge gate REJECTED" in out
    assert "rolled HEAD back" in out
    assert "task done: wrote add.py" in out
    # events are time-offset and ordered
    assert "+   0s" in out
    store.close()


def test_render_audit_empty():
    store, _ = _store()
    gid = store.create_goal(_contract_json(), "/tmp/ws")
    # delete nothing; a fresh goal has at least the goal_created event
    out = render_audit(store.get_goal(gid), store.events(gid), "g")
    assert "Audit Trail" in out
    store.close()


def test_render_runs_lists_goals():
    store, _ = _store()
    _seed(store)
    out = render_runs(store.list_goals(),
                      lambda g: GoalContract.from_json(g["contract"]).goal)
    assert "paused" in out and "ship the thing" in out
    store.close()


def test_cli_audit_and_runs():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "s.db")
        store = Store(db)
        gid = _seed(store)
        store.close()

        r = runner.invoke(main, ["audit", gid, "--db", db])
        assert r.exit_code == 0, r.output
        assert "merge gate REJECTED" in r.output

        r2 = runner.invoke(main, ["runs", "--db", db])
        assert r2.exit_code == 0 and "ship the thing" in r2.output

        r3 = runner.invoke(main, ["audit", "nope", "--db", db])
        assert r3.exit_code != 0
