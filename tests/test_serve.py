"""Phase 1 — `loophole serve` (the web Forge): snapshot + HTTP endpoints."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.request

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.serve import build_snapshot, make_server
from loophole.state import Store


def _store_with_run():
    d = tempfile.mkdtemp(prefix="loophole_serve_")
    store = Store(os.path.join(d, "s.db"))
    gid = store.create_goal(GoalContract(goal="build a parser", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json(), "/ws")
    store.add_task(gid, "write parser.py", task_id="t1")
    store.set_task_status("t1", "running", gid)
    store.log("verify_run", goal_id=gid, payload={"passed": False, "score": 3})
    store.log("write_glob_violation", goal_id=gid, payload={"violations": ["tests/x is protected"]})
    return store, gid


def test_build_snapshot_shape():
    store, gid = _store_with_run()
    snap = build_snapshot(store, gid)
    assert snap["goal"] == "build a parser"
    assert snap["status"] in ("running", "done", "paused", "failed")
    assert any(t["status"] == "running" for t in snap["tasks"])
    kinds = {e["kind"] for e in snap["events"]}
    assert "verify_run" in kinds and "write_glob_violation" in kinds
    # payloads are decoded to objects, not raw JSON strings
    vr = next(e for e in snap["events"] if e["kind"] == "verify_run")
    assert vr["payload"]["passed"] is False
    store.close()


def test_build_snapshot_unknown_goal():
    store, _ = _store_with_run()
    assert build_snapshot(store, "nope") is None
    store.close()


def test_http_serves_page_and_state():
    store, gid = _store_with_run()
    srv = make_server(store, gid, host="127.0.0.1", port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = "http://127.0.0.1:{}".format(srv.server_address[1])
        html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "THE FORGE" in html and "VERIFY GATE" in html

        state = json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read())
        assert state["goal"] == "build a parser"
        assert "events" in state and "tasks" in state

        # unknown path -> 404
        try:
            urllib.request.urlopen(base + "/nope", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()


def test_sse_events_stream():
    import time as _t
    store, gid = _store_with_run()
    srv = make_server(store, gid, host="127.0.0.1", port=0)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        base = "http://127.0.0.1:{}".format(srv.server_address[1])
        resp = urllib.request.urlopen(base + "/events", timeout=5)
        assert resp.headers.get("Content-Type") == "text/event-stream"
        data_line = None
        start = _t.time()
        while _t.time() - start < 5:
            line = resp.readline()
            if line.startswith(b"data: "):
                data_line = line[6:].strip()
                break
        assert data_line, "no SSE data event received"
        snap = json.loads(data_line)
        assert snap["goal"] == "build a parser"
        resp.close()
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()


def _store_with_two_runs():
    import tempfile, os
    d = tempfile.mkdtemp(prefix="loophole_fleet_")
    store = Store(os.path.join(d, "s.db"))
    g1 = store.create_goal(GoalContract(goal="run one", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")]).to_json(), "/ws1")
    store.add_task(g1, "a", task_id="a1"); store.set_task_status("a1", "done", g1)
    store.log("verify_run", goal_id=g1, payload={"passed": True, "score": 1000})
    store.set_goal_status(g1, "done")
    g2 = store.create_goal(GoalContract(goal="run two", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")]).to_json(), "/ws2")
    store.add_task(g2, "b", task_id="b1"); store.set_task_status("b1", "running", g2)
    store.log("write_glob_violation", goal_id=g2, payload={"violations": ["x"]})
    return store, g1, g2


def test_fleet_snapshot_summaries():
    from loophole.serve import fleet_snapshot
    store, g1, g2 = _store_with_two_runs()
    fleet = fleet_snapshot(store)
    assert len(fleet) == 2
    by = {f["id"]: f for f in fleet}
    assert by[g1]["status"] == "done" and by[g1]["verdict"] == "pass" and by[g1]["done"] == 1
    assert by[g2]["status"] == "running" and by[g2]["running"] == 1 and by[g2]["saves"] == 1
    store.close()


def test_http_fleet_and_run_routes():
    store, g1, g2 = _store_with_two_runs()
    srv = make_server(store)               # no default goal -> '/' is the fleet
    th = threading.Thread(target=srv.serve_forever, daemon=True); th.start()
    try:
        base = "http://127.0.0.1:{}".format(srv.server_address[1])
        fleet_html = urllib.request.urlopen(base + "/", timeout=5).read().decode()
        assert "FLEET" in fleet_html and "/api/fleet" in fleet_html

        fleet = json.loads(urllib.request.urlopen(base + "/api/fleet", timeout=5).read())
        assert {f["id"] for f in fleet} == {g1, g2}

        run_html = urllib.request.urlopen(base + "/run?goal=" + g1, timeout=5).read().decode()
        assert "VERIFY GATE" in run_html
        st = json.loads(urllib.request.urlopen(base + "/api/state?goal=" + g2, timeout=5).read())
        assert st["goal"] == "run two"

        # no goal + no default -> 404
        try:
            urllib.request.urlopen(base + "/api/state", timeout=5)
            assert False, "expected 404"
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown(); srv.server_close(); store.close()
