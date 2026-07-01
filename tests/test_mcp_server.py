"""The stdio MCP server: the JSON-RPC handshake, tool discovery, and tool calls —
driven in-process (no subprocess, no live model) against a seeded Store."""
from __future__ import annotations

import io
import json
import os
import tempfile

import pytest

from loophole import mcp_server as M
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.state import Store


def _seed_db(monkeypatch):
    db = os.path.join(tempfile.mkdtemp(prefix="loophole_mcp_"), "s.db")
    monkeypatch.setattr(M, "_default_db", lambda: db)
    st = Store(db)
    c = GoalContract(goal="build a parser",
                     verifiers=[Verifier(kind=VerifierKind.HARD, command="true")])
    gid = st.create_goal(c.to_json(), "/ws")
    t1 = st.add_task(gid, "implement parser.py")
    st.set_task_status(t1, "running", goal_id=gid)
    st.log("task_done", goal_id=gid, task_id=t1, payload={"summary": "parser.py"})
    st.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
    st.set_goal_status(gid, "done")
    return gid


# ---- protocol handshake ----------------------------------------------------

def test_initialize_and_tools_list():
    runs = M.RunManager()
    init = M.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, runs)
    assert init["result"]["protocolVersion"] == M.PROTOCOL_VERSION
    assert init["result"]["serverInfo"]["name"] == "loophole"
    # the 'initialized' notification gets no reply
    assert M.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, runs) is None
    tl = M.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, runs)
    names = {t["name"] for t in tl["result"]["tools"]}
    assert names == {"loophole_run", "loophole_status", "loophole_list"}
    # required fields are declared
    run_tool = next(t for t in tl["result"]["tools"] if t["name"] == "loophole_run")
    assert run_tool["inputSchema"]["required"] == ["goal", "verify"]


def test_unknown_method_errors_but_notifications_are_silent():
    runs = M.RunManager()
    err = M.handle({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}, runs)
    assert err["error"]["code"] == -32601
    assert M.handle({"jsonrpc": "2.0", "method": "some/notification"}, runs) is None


# ---- tools/call: status + list render markdown -----------------------------

def test_status_call_returns_markdown_snapshot(monkeypatch):
    gid = _seed_db(monkeypatch)
    runs = M.RunManager()
    resp = M.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "loophole_status",
                                "arguments": {"goal_id": gid}}}, runs)
    text = resp["result"]["content"][0]["text"]
    assert "⚒ loophole" in text and "verified done" in text
    assert "**Verdict:**" in text


def test_list_call_lists_seeded_run(monkeypatch):
    _seed_db(monkeypatch)
    runs = M.RunManager()
    resp = M.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                     "params": {"name": "loophole_list", "arguments": {}}}, runs)
    text = resp["result"]["content"][0]["text"]
    assert "build a parser" in text and "done" in text


def test_call_missing_required_arg_is_error(monkeypatch):
    _seed_db(monkeypatch)
    runs = M.RunManager()
    resp = M.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                     "params": {"name": "loophole_run", "arguments": {"goal": "x"}}}, runs)
    assert resp["result"]["isError"] is True


def test_unknown_tool_is_error():
    runs = M.RunManager()
    resp = M.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                     "params": {"name": "nope", "arguments": {}}}, runs)
    assert resp["result"]["isError"] is True


# ---- the stdio loop glues it together --------------------------------------

def test_serve_loop_over_stdio(monkeypatch):
    gid = _seed_db(monkeypatch)
    requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "loophole_status", "arguments": {"goal_id": gid}}}),
    ]) + "\n"
    out = io.StringIO()
    M.serve(stdin=io.StringIO(requests), stdout=out)
    lines = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    # two replies (initialize + tools/call); the notification produced none
    assert len(lines) == 2
    assert lines[0]["result"]["serverInfo"]["name"] == "loophole"
    assert "verified done" in lines[1]["result"]["content"][0]["text"]
