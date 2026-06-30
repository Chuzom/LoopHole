"""Council CRITICALs: CSEC-1 (symlink escape) + CARCH-1 (orphaned running tasks)."""
from __future__ import annotations

import os
import subprocess
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.loop import _reset_orphan_running
from loophole.state import Store, Task
from loophole.verifier import _read, _snapshot


# ---- CSEC-1: symlink escape ------------------------------------------------

def test_read_refuses_symlinks():
    with tempfile.TemporaryDirectory() as d:
        secret = os.path.join(d, "secret.txt")
        with open(secret, "w") as f:
            f.write("TOP-SECRET-HOST-FILE")
        link = os.path.join(d, "evil")
        os.symlink(secret, link)
        # a real file still reads; the symlink reads as absent (no follow)
        assert _read(secret) == b"TOP-SECRET-HOST-FILE"
        assert _read(link) is None


def test_snapshot_skips_symlinks_to_host_files():
    with tempfile.TemporaryDirectory() as outside:
        secret = os.path.join(outside, "id_rsa")
        with open(secret, "w") as f:
            f.write("PRIVATE-KEY-MATERIAL")
        with tempfile.TemporaryDirectory() as cand:
            with open(os.path.join(cand, "ok.py"), "w") as f:
                f.write("x = 1\n")
            os.symlink(secret, os.path.join(cand, "leak"))   # agent-committed symlink
            snap = _snapshot(cand)
            assert "x = 1" in snap                    # real file present
            assert "PRIVATE-KEY-MATERIAL" not in snap  # symlink target NOT leaked
            assert "leak" not in snap


# (The no-git copytree(symlinks=True) fix is covered indirectly: any checkout —
# git archive or copytree — preserves the symlink, and _read/_snapshot above refuse
# to follow it. Forcing Integration's no-git lane in a unit test is impractical
# since it auto-inits git.)


# ---- CARCH-1: orphaned running tasks ---------------------------------------

def test_reset_orphan_running_tasks():
    d = tempfile.mkdtemp(prefix="loophole_orph_")
    store = Store(os.path.join(d, "s.db"))
    gid = store.create_goal(GoalContract(goal="g", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")]).to_json(), "/ws")
    # seed two tasks: one stuck 'running' (crash orphan), one 'done'
    store.add_task(gid, "orphan", task_id="t1")
    store.set_task_status("t1", "running", gid)
    store.add_task(gid, "done", task_id="t2")
    store.set_task_status("t2", "done", gid)

    n = _reset_orphan_running(store, gid)
    assert n == 1
    by_id = {t.id: t.status for t in store.tasks_for_goal(gid)}
    assert by_id["t1"] == "pending"   # orphan reset so the loop re-runs it
    assert by_id["t2"] == "done"      # finished work untouched
    store.close()
