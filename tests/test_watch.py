"""Phase 0 — `loophole watch` (THE FORGE) frame rendering + CLI."""
from __future__ import annotations

import os
import tempfile

from click.testing import CliRunner

from loophole.cli import main
from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.state import Store, Task
from loophole.watch import render_frame


def _store():
    d = tempfile.mkdtemp(prefix="loophole_watch_")
    return Store(os.path.join(d, "s.db")), d


def _cjson():
    return GoalContract(goal="build the parser", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json()


def test_frame_shows_swarm_gate_and_boundary_reject():
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")
    store.log("verify_run", goal_id=gid, payload={"passed": False, "score": -2})
    store.log("write_glob_violation", goal_id=gid, task_id="t9",
              payload={"violations": ["secrets/keys.txt outside allowed writes"]})
    tasks = [
        Task(id="t1", goal_id=gid, description="write parser.py", status="running"),
        Task(id="t2", goal_id=gid, description="tokens.py", status="done"),
    ]
    frame = render_frame("build the parser", "running", tasks, store.events(gid),
                         color=False)
    assert "THE FORGE" in frame
    assert "GOAL" in frame and "build the parser" in frame
    assert "THE SWARM" in frame and "write parser.py" in frame   # running lane
    assert "VERIFY GATE" in frame
    # a boundary rejection newer than the last verify dominates the headline
    assert "REJECT" in frame
    assert "BOUNDARY" in frame and "held ×1" in frame
    store.close()


def test_frame_shows_pass_when_latest_verify_passes():
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")
    store.log("write_glob_violation", goal_id=gid, payload={"violations": ["x"]})
    store.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
    frame = render_frame("g", "done", [
        Task(id="t1", goal_id=gid, description="done thing", status="done")],
        store.events(gid), color=False)
    assert "PASS" in frame
    assert "▓▓▓▓▓▓▓▓▓▓" in frame      # full progress bar on pass
    store.close()


def test_frame_empty_run():
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")
    frame = render_frame("g", "running", [], store.events(gid), color=False)
    assert "no agents active yet" in frame
    assert "waiting" in frame          # gate idle
    store.close()


def test_watch_during_runs_callable_and_renders():
    import io
    from loophole.watch import watch_during
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")

    def _fake_run():
        # simulate the loop emitting an event then finishing
        store.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
        store.set_goal_status(gid, "done")
        return "OUTCOME-OK"

    buf = io.StringIO()
    result = watch_during(store, gid, _fake_run, interval=0.05, out=buf)
    assert result == "OUTCOME-OK"          # returns the callable's result
    assert "THE FORGE" in buf.getvalue()   # rendered at least the final frame
    store.close()


def test_watch_during_propagates_errors():
    import io
    from loophole.watch import watch_during
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")

    def _boom():
        raise RuntimeError("loop failed")

    try:
        watch_during(store, gid, _boom, interval=0.05, out=io.StringIO())
        assert False, "expected the error to propagate"
    except RuntimeError as e:
        assert "loop failed" in str(e)
    store.close()


def test_cli_watch_once():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "s.db")
        store = Store(db)
        gid = store.create_goal(_cjson(), "/ws")
        store.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
        store.close()

        r = runner.invoke(main, ["watch", gid, "--once", "--db", db])
        assert r.exit_code == 0, r.output
        assert "THE FORGE" in r.output and "VERIFY GATE" in r.output

        r2 = runner.invoke(main, ["watch", "nope", "--once", "--db", db])
        assert r2.exit_code != 0


class _T:
    def __init__(self, i, d, st):
        self.id = i; self.description = d; self.status = st


def test_swarm_shows_funny_animated_agents():
    from loophole.watch import render_frame, _ACTIVITIES
    tasks = [_T("t0", "write parser.py", "running"), _T("t1", "write tokens.py", "running")]
    f = render_frame("g", "running", tasks, [], color=False, frame=0)
    assert any(face in f for face in ["(o_o)", "(0_0)", "(-_-)", "(>_>)", "(<_<)"])
    assert any(a in f for a in _ACTIVITIES)            # a goofy activity line
    assert "write parser.py" in f and "write tokens.py" in f


def test_agent_vocabulary_is_ascii_for_cross_terminal_parity():
    # Claude Code / Cursor / Codex must show the swarm identically -> ASCII only.
    from loophole.watch import _FACE_WORK, _SPN, _FACE_BLINK, _FACE_DONE, _FACE_FAIL, _ACTIVITIES
    for s in _FACE_WORK + list(_SPN) + [_FACE_BLINK, _FACE_DONE, _FACE_FAIL] + _ACTIVITIES:
        s.encode("ascii")          # raises UnicodeEncodeError on any non-ASCII


def test_animation_advances_with_frame():
    from loophole.watch import render_frame
    t = [_T("abc", "do a thing", "running")]
    a = render_frame("g", "running", t, [], color=False, frame=0)
    b = render_frame("g", "running", t, [], color=False, frame=1)
    assert a != b                  # spinner / merge-cart / shimmer moved


def test_run_watch_renders_funny_swarm_end_to_end():
    """The `run --watch` path (watch_during) must show the animated swarm WHILE the
    run executes and the final PASS frame when it finishes."""
    import io, time
    from loophole.watch import watch_during
    store, _ = _store()
    gid = store.create_goal(_cjson(), "/ws")
    store.add_task(gid, "implement parser.py", task_id="t1")

    def _fake_run():
        store.set_task_status("t1", "running", gid)
        time.sleep(0.25)                       # let the watcher draw the running swarm
        store.set_task_status("t1", "done", gid)
        store.log("verify_run", goal_id=gid, payload={"passed": True, "score": 1000})
        store.set_goal_status(gid, "done")
        return "DONE"

    buf = io.StringIO()
    res = watch_during(store, gid, _fake_run, interval=0.03, out=buf)
    s = buf.getvalue()
    assert res == "DONE"
    assert "THE FORGE" in s and "VERIFY GATE" in s
    assert any(f in s for f in ["(o_o)", "(0_0)", "(>_>)", "(<_<)", "(-_-)"])  # an agent worked
    assert "(^_^)v" in s and "PASS" in s        # merged-agent face + final verdict
    store.close()


def test_run_watch_default_and_flag_parse():
    # THE FORGE is now the default for interactive `run`; both flag forms must parse,
    # and a non-TTY invocation must NOT error on the option itself.
    from click.testing import CliRunner
    from loophole.cli import main
    r = CliRunner()
    for flag in ("--watch", "--no-watch"):
        out = r.invoke(main, ["run", flag, "--contract", "/nope/missing.json"]).output
        assert "no such option" not in out.lower()      # the flag is recognized
