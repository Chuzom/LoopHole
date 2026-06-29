from __future__ import annotations

import os
import tempfile

from loophole.state import Store


def _store():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    return Store(path)


def test_goal_lifecycle():
    s = _store()
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    assert s.get_goal(gid)["status"] == "running"
    s.set_goal_status(gid, "done")
    assert s.get_goal(gid)["status"] == "done"
    s.close()


def test_task_crud_and_deps():
    s = _store()
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    a = s.add_task(gid, "task a", writes=["x.py"])
    b = s.add_task(gid, "task b", depends_on=[a], reads=["x.py"])
    tasks = s.tasks_for_goal(gid)
    assert len(tasks) == 2
    tb = s.get_task(b)
    assert tb.depends_on == [a]
    assert tb.reads == ["x.py"]
    s.close()


def test_attempts_increment():
    s = _store()
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    t = s.add_task(gid, "t")
    assert s.incr_attempts(t) == 1
    assert s.incr_attempts(t) == 2
    s.close()


def test_events_logged():
    s = _store()
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    s.log("custom", goal_id=gid, payload={"k": 1})
    kinds = [e["kind"] for e in s.events(gid)]
    assert "goal_created" in kinds and "custom" in kinds
    s.close()
