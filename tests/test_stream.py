"""The append-only milestone STREAM renderer + the Claude Desktop markdown snapshot.

Every line is a pure function of the event log, so we assert the mapping directly
(no terminal needed) and then drive a real Store end-to-end."""
from __future__ import annotations

import io
import os
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.state import Store
from loophole import stream as S
from loophole.stream import Stream, render_markdown_snapshot, stream_run


def ev(kind, task_id=None, **payload):
    return {"kind": kind, "task_id": task_id, "payload": payload or None}


# ---- pure mapping: one event -> one line (or None) -------------------------

def test_each_milestone_is_one_line_once():
    s = Stream(color=False)
    assert "queued" in s.consume(ev("task_created", "t1", description="parser.py"))
    assert "working" in s.consume(ev("task_status", "t1", status="running"))
    assert s.consume(ev("task_status", "t1", status="pending")) is None   # noise muted
    assert "wrote" in s.consume(ev("agent_step", "t1", tool="write_file", note="parser.py"))
    line = s.consume(ev("task_done", "t1", summary="parser.py"))
    assert "merged" in line and "[1 merged]" in line and s.merged == 1


def test_boundary_beats_are_loud():
    s = Stream(color=False)
    rej = s.consume(ev("merge_gate_reject", "t1", failures=["FAILED test_parser.py"]))
    assert "REJECT" in rej and "test_parser" in rej and s.rejected == 1
    blk = s.consume(ev("write_glob_violation", "t1", violations=["../etc/passwd"]))
    assert "BLOCKED" in blk and s.blocked == 1


def test_verify_gate_pass_and_retry_numbering():
    s = Stream(color=False)
    assert "not green" in s.consume(ev("verify_run", passed=False, score=0))
    s.consume(ev("task_status", "t1", status="running"))          # run 1
    retry = s.consume(ev("task_status", "t1", status="running"))  # run 2
    assert "retry 2" in retry
    ok = s.consume(ev("verify_run", passed=True, score=1000))
    assert "VERIFIED" in ok and s.rounds == 2


def test_stable_agent_numbers():
    s = Stream(color=False)
    a = s.consume(ev("task_status", "t1", status="running"))
    b = s.consume(ev("task_status", "t2", status="running"))
    assert "①" in a and "②" in b
    again = s.consume(ev("agent_step", "t1", tool="run_shell", note="pytest"))
    assert "①" in again          # t1 keeps its number


def test_muted_kinds_return_none():
    s = Stream(color=False)
    for k in ("goal_created", "goal_status", "executor_network", "task_orphans_reconciled"):
        assert s.consume(ev(k)) is None


def test_summary_reads_like_a_verdict():
    s = Stream(color=False)
    s.consume(ev("task_done", "t1", summary="x"))
    s.consume(ev("merge_gate_reject", "t1", failures=["boom"]))
    s.consume(ev("verify_run", passed=True, score=1000))
    out = s.summary("done", elapsed_s=19)
    assert "VERIFIED DONE" in out and "1 merged" in out and "rejected before accept" in out


# ---- IDE-terminal portability: ASCII fallback + capability detection -------

def test_ascii_mode_folds_every_glyph():
    s = Stream(color=False, ascii_only=True)
    assert s.fold("⚒ ✓ ✗ · → …") == "# v x - -> ..."
    run = s.consume(ev("task_status", "t1", status="running"))
    assert "1)" in run and "①" not in run                    # numbers fold too
    done = s.consume(ev("task_done", "t1", summary="parser"))
    assert "v" in done and "✓" not in done
    rej = s.consume(ev("merge_gate_reject", "t1", failures=["boom"]))
    assert "x" in rej and "✗" not in rej
    assert "✗" not in s.summary("failed") and "x NOT DONE" in s.summary("failed")


def test_capability_detection(monkeypatch):
    class Tty:
        encoding = "utf-8"
        def isatty(self):
            return True
    class Pipe:
        encoding = "ascii"
        def isatty(self):
            return False
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("LOOPHOLE_ASCII", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    assert S.supports_color(Tty()) and S.supports_unicode(Tty())
    assert not S.supports_color(Pipe())              # not a tty -> no color
    assert not S.supports_unicode(Pipe())            # ascii encoding -> fold
    monkeypatch.setenv("NO_COLOR", "1")
    assert not S.supports_color(Tty())               # NO_COLOR honoured
    monkeypatch.setenv("LOOPHOLE_ASCII", "1")
    assert not S.supports_unicode(Tty())             # forced ASCII honoured
    monkeypatch.delenv("LOOPHOLE_ASCII", raising=False)
    monkeypatch.setenv("TERM", "dumb")
    assert not S.supports_unicode(Tty())             # TERM=dumb -> fold


# ---- end to end against a real Store ---------------------------------------

def _seed_store():
    st = Store(os.path.join(tempfile.mkdtemp(prefix="loophole_stream_"), "s.db"))
    c = GoalContract(goal="build a parser",
                     verifiers=[Verifier(kind=VerifierKind.HARD, command="true")])
    gid = st.create_goal(c.to_json(), "/ws")
    t1 = st.add_task(gid, "implement parser.py")
    st.set_task_status(t1, "running", goal_id=gid)
    st.log("agent_step", goal_id=gid, task_id=t1, payload={"tool": "write_file", "note": "parser.py"})
    st.log("merge_gate_reject", goal_id=gid, task_id=t1, payload={"failures": ["FAILED test_x"]})
    st.log("task_done", goal_id=gid, task_id=t1, payload={"summary": "parser.py"})
    st.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
    st.set_goal_status(gid, "done")
    return st, gid


def test_stream_run_prints_lines_then_verdict():
    st, gid = _seed_store()
    buf = io.StringIO()
    stream_run(st, gid, out=buf, interval=0.01)
    s = buf.getvalue()
    assert "⚒  loophole" in s and "build a parser" in s
    assert "wrote" in s and "REJECT" in s and "VERIFIED" in s
    assert "VERIFIED DONE" in s          # closing verdict
    # append-only: the goal text banner appears exactly once (no repaint)
    assert s.count("build a parser") == 1
    st.close()


def test_markdown_snapshot_for_claude_desktop():
    st, gid = _seed_store()
    md = render_markdown_snapshot(st, gid)
    assert md.startswith("### ⚒ loophole")
    assert "✅ verified done" in md
    assert "| # | task | state |" in md          # agent table
    assert "**Milestones**" in md
    assert "**Verdict:**" in md and "rejected before accept" in md
    st.close()


# ---- Chuzom auto-detection (loophole routes through Chuzom when it's up) ----

def test_detect_chuzom_signals(monkeypatch, tmp_path):
    from loophole import provider as P
    import shutil as _sh
    for v in ("CHUZOM_URL", "CHUZOM_TIER_COMPLEX", "CHUZOM_TIER_MODERATE"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(P.os.path, "isdir", lambda p: False)   # no ~/.chuzom
    monkeypatch.setattr(_sh, "which", lambda n: None)          # no chuzom binary
    assert P.detect_chuzom() is False
    assert P.default_model() == "ollama:qwen3-coder:30b"
    monkeypatch.setenv("CHUZOM_URL", "http://localhost:8000/route")
    assert P.detect_chuzom() is True
    assert P.default_model() == "chuzom:auto"
