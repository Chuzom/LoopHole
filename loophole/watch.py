"""Phase 0 of the visual experience — `loophole watch`, "THE FORGE".

A live, full-screen terminal view of the swarm at work, rendered from the event
stream the store already emits (no engine changes). The signature beat is the
VERIFY GATE: every verification flashes PASS or REJECT, and the boundary shows when
it caught a cheat. Pure stdlib (ANSI) — no new dependency, and render_frame() is a
pure function so it's fully testable.

Metaphor: a swarm of agents (lanes) feeds a central GATE; the gate decides 'done'.
"""

from __future__ import annotations

import time
from typing import Any, List, Optional

# ---- ANSI ------------------------------------------------------------------
_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
    "cyan": "\033[36m", "magenta": "\033[35m", "blue": "\033[34m",
}
_CLEAR_HOME = "\033[2J\033[H"

_RUNNING_GLYPH = "\033[36m⚙\033[0m"   # only used with color

_BOUNDARY_KINDS = {"write_glob_violation", "merge_gate_reject",
                   "soft_fail_closed", "boundary_violation"}

# ---- the funny swarm vocabulary (ASCII-only so it renders identically in any
# terminal — Claude Code, Cursor, Codex, plain ssh) -------------------------
_SPN = "|/-\\"                                   # universal ASCII spinner
_FACE_OPEN = "(o_o)"
_FACE_BLINK = "(-_-)"
_FACE_WORK = ["(o_o)", "(o_o)", "(0_0)", "(o_o)", "(>_>)", "(o_o)", "(<_<)"]
_FACE_DONE = "(^_^)v"
_FACE_FAIL = "(x_x)"
# little goofy status lines — what an agent is "doing" while it grinds
_ACTIVITIES = [
    "hammering code", "summoning a regex", "bribing the linter",
    "renaming x -> data", "writing a test (ugh)", "deleting a TODO",
    "fighting the type checker", "googling the stacktrace",
    "refactoring in circles", "consulting the rubber duck",
    "untangling a merge", "appeasing the verifier", "naming things (hard)",
    "hunting an off-by-one", "petting the edge cases", "arguing with git",
]


def _seed(s: str) -> int:
    """Deterministic per-agent seed (builtin hash() is salted per process)."""
    return sum((i + 1) * ord(c) for i, c in enumerate(s)) & 0xFFFF


def _busy_bar(seed: int, frame: int, total: int = 8) -> str:
    """A knight-rider shimmer so an agent always looks alive (indeterminate)."""
    span = total * 2 - 2
    p = (seed + frame) % span
    pos = p if p < total else span - p
    return "".join("▓" if i == pos else "░" for i in range(total))


def _col(s: str, name: str, color: bool) -> str:
    return (_C[name] + s + _C["reset"]) if color else s


def _bar(filled: int, total: int = 10) -> str:
    filled = max(0, min(total, filled))
    return "▓" * filled + "░" * (total - filled)


def _payload(e: Any) -> dict:
    import json
    raw = e["payload"] if "payload" in e.keys() else None
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _latest(events: List[Any], kind: str) -> Optional[Any]:
    for e in reversed(events):
        if e["kind"] == kind:
            return e
    return None


def _agent_line(t: Any, idx: int, frame: int, color: bool) -> str:
    """One funny, animated agent: a face that blinks/looks around, a spinner, and a
    goofy 'what it's doing' line that drifts over time. Deterministic per (id, frame)."""
    seed = _seed(getattr(t, "id", "") or "") ^ (idx * 131)
    blink = ((seed // 7 + frame) // 3) % 9 == 0
    face = _FACE_BLINK if blink else _FACE_WORK[(seed + frame // 6) % len(_FACE_WORK)]
    spin = _SPN[frame % 4]
    act = _ACTIVITIES[(seed + frame // 5) % len(_ACTIVITIES)]
    name = (t.description or "")[:22]
    return "   {} {}  {:<22} {:<26} {}".format(
        _col(face, "cyan", color), spin, name, _col(act, "dim", color),
        _busy_bar(seed, frame))


def render_frame(goal_text: str, status: str, tasks: List[Any], events: List[Any],
                 color: bool = True, width: int = 72, frame: int = 0) -> str:
    line = "─" * width
    out: List[str] = []
    add = out.append

    # ---- header ----
    spark = _SPN[frame % 4]
    add(_col("╔═ LOOPHOLE ═ THE FORGE " + spark + " " + "═" * (width - 25) + "╗", "magenta", color))
    add(" {}  {}".format(_col("GOAL", "bold", color), goal_text[:width - 16])
        + "  " + _col("[" + status + "]", "yellow", color))

    # ---- the swarm: a little crew of agents, each doing something silly ----
    add(_col(" " + "─" * ((width - 11) // 2) + " THE SWARM " + "─" * ((width - 11) // 2), "dim", color))
    running = [t for t in tasks if t.status == "running"]
    done = [t for t in tasks if t.status == "done"]
    failed = [t for t in tasks if t.status == "failed"]
    if not running and not done:
        add("  " + _col("· no agents active yet", "dim", color)
            + _col("  " + _FACE_BLINK + " (napping)", "dim", color))
    for i, t in enumerate(running[:5]):
        add(_agent_line(t, i, frame, color))
    for t in done[-2:]:
        add("   " + _col(_FACE_DONE, "green", color) + "    "
            + (t.description or "")[:width - 18] + _col("  merged ✓", "green", color))
    for t in failed[-1:]:
        add("   " + _col(_FACE_FAIL, "red", color) + "     "
            + (t.description or "")[:width - 18] + _col("  bonked ✗ (retrying)", "red", color))

    # ---- merge-train + VERIFY GATE (the hero beat) ----
    add(_col(" " + line[1:], "dim", color))
    vr = _latest(events, "verify_run")
    bnd = _latest_boundary(events)
    verdict, vcolor = _verdict(vr, bnd, color)
    # an animated little cart trundling toward the gate carrying a candidate
    track = 14
    pos = frame % (track + 1)
    cart = " " * pos + "[o-o]" + "─" * (track - pos) + "▶"
    add("   merge-train  " + _col(cart, "blue", color)
        + "  " + _col("⊟ gate", vcolor, color) + "   ({} merged)".format(len(done)))
    # centered gate box; a fresh verdict makes it "flash" with chevrons each frame
    fresh = bool(events) and events[-1]["kind"] in (_BOUNDARY_KINDS | {"verify_run"})
    flash = fresh and frame % 2 == 0
    mark = "»" if flash else " "
    gate_text = "  VERIFY GATE  {} {} {} ".format(mark, verdict, mark[::-1] or " ")
    boxw = len(gate_text)
    pad = " " * max(0, (width - boxw - 2) // 2)
    add(pad + _col("┌" + "─" * boxw + "┐", vcolor, color))
    add(pad + _col("│" + gate_text + "│", vcolor, color))
    add(pad + _col("└" + "─" * boxw + "┘", vcolor, color))

    # ---- boundary shield ----
    saves = [e for e in events if e["kind"] in _BOUNDARY_KINDS]
    if saves:
        last = _boundary_reason(saves[-1])
        add(" " + _col("⛨ BOUNDARY", "green", color) + " held ×{}".format(len(saves))
            + "   last: " + last[:width - 28])
    else:
        add(" " + _col("⛨ BOUNDARY", "dim", color) + " no cheats attempted   "
            + _col("(>_>) watching", "dim", color))

    # ---- progress ----
    score = int(_payload(vr).get("score", 0)) if vr else 0
    passed = bool(_payload(vr).get("passed")) if vr else False
    filled = 10 if passed else max(0, min(9, score // 100))
    rounds = len([e for e in events if e["kind"] == "verify_run"])
    add(" progress {}  score {}   round {}   {} saves".format(
        _bar(filled), score, rounds, len(saves)))
    add(_col("╚" + "═" * width + "╝", "magenta", color))
    return "\n".join(out)


def _latest_boundary(events: List[Any]) -> Optional[Any]:
    for e in reversed(events):
        if e["kind"] in _BOUNDARY_KINDS:
            return e
    return None


def _boundary_reason(e: Any) -> str:
    p = _payload(e)
    if e["kind"] == "write_glob_violation":
        return "blocked write outside allowlist: " + "; ".join(p.get("violations", []))
    if e["kind"] == "merge_gate_reject":
        return "merge rejected: " + "; ".join((p.get("failures") or []))
    if e["kind"] == "soft_fail_closed":
        return "paused for human: " + str(p.get("reason", ""))
    return e["kind"]


def _verdict(verify_event: Any, boundary_event: Any, color: bool) -> tuple:
    # A boundary rejection NEWER than the last verify dominates the headline.
    if verify_event is None and boundary_event is None:
        return ("…waiting", "dim")
    newest_boundary = (boundary_event is not None and
                       (verify_event is None or boundary_event["seq"] > verify_event["seq"]))
    if newest_boundary:
        return ("✗ REJECT", "red")
    p = _payload(verify_event)
    if p.get("passed"):
        return ("✓ PASS", "green")
    return ("✗ fail", "red")


def watch_during(store: Any, goal_id: str, run_callable, interval: float = 0.7,
                 out: Any = None):
    """Run ``run_callable()`` in a background thread while live-rendering THE FORGE
    in the foreground; return whatever run_callable returns.

    Used by ``loophole run --watch`` so the user watches the swarm work instead of a
    scrolling log. The store is cross-thread safe (check_same_thread=False + lock).
    """
    import sys
    import threading
    import time
    from .contract import GoalContract
    out = out or sys.stdout
    color = out.isatty() if hasattr(out, "isatty") else False
    box: dict = {}

    def _runner():
        try:
            box["result"] = run_callable()
        except BaseException as e:   # surface in the main thread after join
            box["error"] = e

    tick = [0]

    def _draw():
        g = store.get_goal(goal_id)
        if not g:
            return
        body = render_frame(GoalContract.from_json(g["contract"]).goal, g["status"],
                            store.tasks_for_goal(goal_id), store.events(goal_id),
                            color=color, frame=tick[0])
        tick[0] += 1
        out.write(_CLEAR_HOME + body + "\n")
        out.flush()

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    while t.is_alive():
        _draw()
        time.sleep(interval)
    t.join()
    _draw()  # final frame (terminal state)
    if "error" in box:
        raise box["error"]
    return box.get("result")


def run_watch(store: Any, goal_id: str, interval: float = 1.0, once: bool = False,
              out: Any = None) -> None:
    """Live loop: redraw the Forge until the goal reaches a terminal state."""
    import sys
    from .contract import GoalContract
    out = out or sys.stdout
    terminal = {"done", "failed", "paused"}
    tick = 0
    while True:
        g = store.get_goal(goal_id)
        if not g:
            out.write("no such goal: {}\n".format(goal_id))
            return
        goal_text = GoalContract.from_json(g["contract"]).goal
        body = render_frame(goal_text, g["status"], store.tasks_for_goal(goal_id),
                            store.events(goal_id),
                            color=out.isatty() if hasattr(out, "isatty") else False,
                            frame=tick)
        tick += 1
        out.write(("" if once else _CLEAR_HOME) + body + "\n")
        out.flush()
        if once or g["status"] in terminal:
            return
        time.sleep(interval)
