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


def render_frame(goal_text: str, status: str, tasks: List[Any],
                 events: List[Any], color: bool = True, width: int = 72) -> str:
    line = "─" * width
    out: List[str] = []
    add = out.append

    # ---- header ----
    add(_col("╔═ LOOPHOLE ═ THE FORGE " + "═" * (width - 23) + "╗", "magenta", color))
    add(" {}  {}".format(_col("GOAL", "bold", color), goal_text[:width - 16])
        + "  " + _col("[" + status + "]", "yellow", color))

    # ---- the swarm (running lanes + recently done) ----
    add(_col(" " + "─" * ((width - 11) // 2) + " THE SWARM " + "─" * ((width - 11) // 2), "dim", color))
    running = [t for t in tasks if t.status == "running"]
    done = [t for t in tasks if t.status == "done"]
    failed = [t for t in tasks if t.status == "failed"]
    if not running and not done:
        add("  " + _col("· no agents active yet", "dim", color))
    lane = 1
    for t in running[:5]:
        glyph = _col("⚙ run", "cyan", color)
        add("  lane{}  {}  {}".format(lane, glyph, t.description[:width - 18]))
        lane += 1
    for t in done[-2:]:
        add("  " + _col("✓ done", "green", color) + "   " + t.description[:width - 14])
    for t in failed[-1:]:
        add("  " + _col("✗ fail", "red", color) + "   " + t.description[:width - 14])

    # ---- merge-train + VERIFY GATE (the hero beat) ----
    add(_col(" " + line[1:], "dim", color))
    vr = _latest(events, "verify_run")
    bnd = _latest_boundary(events)
    verdict, vcolor = _verdict(vr, bnd, color)
    add("   merge-train {}   candidate {}    ({} merged)".format(
        _col("▶▶▶", "blue", color), _col("──────▶", "blue", color), len(done)))
    # centered gate box, colored as a whole so ANSI never breaks the column math
    gate_text = "  VERIFY GATE   " + verdict + "  "
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
        add(" " + _col("⛨ BOUNDARY", "dim", color) + " no cheats attempted")

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


def run_watch(store: Any, goal_id: str, interval: float = 1.0, once: bool = False,
              out: Any = None) -> None:
    """Live loop: redraw the Forge until the goal reaches a terminal state."""
    import sys
    from .contract import GoalContract
    out = out or sys.stdout
    terminal = {"done", "failed", "paused"}
    while True:
        g = store.get_goal(goal_id)
        if not g:
            out.write("no such goal: {}\n".format(goal_id))
            return
        goal_text = GoalContract.from_json(g["contract"]).goal
        frame = render_frame(goal_text, g["status"], store.tasks_for_goal(goal_id),
                             store.events(goal_id), color=out.isatty() if hasattr(out, "isatty") else False)
        out.write(("" if once else _CLEAR_HOME) + frame + "\n")
        out.flush()
        if once or g["status"] in terminal:
            return
        time.sleep(interval)
