"""`loophole serve --demo` — a self-driving live demo of THE FORGE, no LLM needed.

Seeds a small fleet of runs and drives one "live" run through a dramatic timeline
(agents spin up → a merge gate REJECTS → the boundary blocks a cheat → a retry
PASSES → done → it loops) by writing the same events the real engine emits. The web
UI animates exactly as it would on a real run.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any, List, Tuple

from .contract import GoalContract, Verifier, VerifierKind


def _mk_goal(store: Any, goal: str) -> str:
    return store.create_goal(GoalContract(goal=goal, verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json(), "/demo")


def seed_and_simulate(store: Any, interval: float = 1.1) -> Tuple[List[str], threading.Event]:
    """Seed a fleet + start a daemon timeline thread. Returns (goal_ids, stop_event)."""
    # --- a couple of finished/varied runs so the FLEET grid looks alive ---
    g_done = _mk_goal(store, "add a /health endpoint returning 200")
    for i, d in enumerate(["impl /health route", "wire it into the app", "add a test"]):
        store.add_task(g_done, d, task_id="d{}".format(i))
        store.set_task_status("d{}".format(i), "done", g_done)
    store.log("verify_run", goal_id=g_done, payload={"passed": True, "score": 1000})
    store.set_goal_status(g_done, "done")

    g_fail = _mk_goal(store, "migrate auth to OAuth2 without weakening the tests")
    store.add_task(g_fail, "swap auth backend", task_id="f0")
    store.set_task_status("f0", "failed", g_fail)
    store.log("merge_gate_reject", goal_id=g_fail, task_id="f0",
              payload={"failures": ["FAILED tests/test_auth.py::test_login"]})
    store.set_goal_status(g_fail, "paused")

    # --- the LIVE run that animates ---
    live = _mk_goal(store, "build a JSON parser with full test coverage")
    for i, d in enumerate(["scanner.py", "tokens.py", "parser.py", "errors.py"]):
        store.add_task(live, "implement " + d, task_id="t{}".format(i))

    def S(tid, status):
        store.set_task_status(tid, status, live)

    def timeline():
        return [
            lambda: store.set_goal_status(live, "running"),
            lambda: (S("t0", "running"), S("t1", "running")),
            lambda: (S("t1", "done"), store.log("task_done", goal_id=live, task_id="t1",
                     payload={"summary": "wrote tokens.py"})),
            lambda: (S("t2", "running"),
                     store.log("write_glob_violation", goal_id=live, task_id="t2",
                               payload={"violations": ["tests/test_core.py is protected"]})),
            lambda: store.log("verify_run", goal_id=live, payload={"passed": False, "score": 4}),
            lambda: (S("t2", "failed"),
                     store.log("merge_gate_reject", goal_id=live, task_id="t2",
                               payload={"failures": ["FAILED tests/test_parser.py::test_nested"]})),
            lambda: (S("t2", "running"), S("t3", "running")),    # retry
            lambda: (S("t0", "done"), S("t3", "done")),
            lambda: (S("t2", "done"),
                     store.log("verify_run", goal_id=live, payload={"passed": True, "score": 1000})),
            lambda: store.set_goal_status(live, "done"),
            lambda: time.sleep(interval * 2),                    # hold on DONE
            # reset for the next loop
            lambda: ([S("t{}".format(i), "pending") for i in range(4)],
                     store.set_goal_status(live, "running")),
        ]

    stop = threading.Event()

    def run():
        for step in itertools.cycle(timeline()):
            if stop.is_set():
                return
            try:
                step()
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=run, daemon=True).start()
    return [g_done, g_fail, live], stop
