"""A run's terminal reason is persisted and surfaced (UX audit findings 1-2)."""

import json
import os

from loophole.audit import _summary
from loophole.state import Store


def test_set_goal_status_persists_detail(tmp_path):
    store = Store(os.path.join(str(tmp_path), "s.db"))
    gid = store.create_goal("{}", str(tmp_path))
    store.set_goal_status(gid, "paused",
                          detail="degenerate plan repeated 3x — escalating to human")
    g = store.get_goal(gid)
    assert g["detail"].startswith("degenerate plan")
    # and the goal_status audit event carries the reason
    evs = [e for e in store.events(gid) if e["kind"] == "goal_status"]
    assert json.loads(evs[-1]["payload"])["detail"].startswith("degenerate plan")
    # detail-less updates don't erase a stored reason
    store.set_goal_status(gid, "running")
    assert store.get_goal(gid)["detail"].startswith("degenerate plan")


def test_audit_renders_goal_status_detail():
    s = _summary("goal_status", {"status": "paused",
                                 "detail": "budget exhausted"})
    assert s == "status -> paused: budget exhausted"
    assert _summary("goal_status", {"status": "done"}) == "status -> done"


def test_migration_adds_detail_to_old_db(tmp_path):
    import sqlite3
    db = os.path.join(str(tmp_path), "old.db")
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE goals (
        id TEXT PRIMARY KEY, contract TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'running', workspace TEXT NOT NULL,
        base_commit TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
    conn.commit()
    conn.close()
    store = Store(db)                     # must not raise; must add the column
    gid = store.create_goal("{}", str(tmp_path))
    store.set_goal_status(gid, "failed", detail="planner gave up")
    assert store.get_goal(gid)["detail"] == "planner gave up"
