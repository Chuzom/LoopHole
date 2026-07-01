"""The live run view, redesigned as an append-only milestone STREAM.

The old FORGE full-screen-repainted a mostly-static dashboard every tick, so each
frame was ~90% identical to the last — it read as "the same message over and over"
and was unusable when piped, in CI, or in Claude Desktop (no ANSI there at all).

This renderer does what good CLIs do (git, `docker build`, cargo): it emits ONE
clean line per real milestone, printed once, never repainted. The event log drives
it, so every line is a real thing that happened — a task queued, an agent's actual
tool call, the verify GATE deciding, the BOUNDARY blocking a cheat. On a TTY a
single transient status line at the bottom gives liveness (a spinner + "2 agents
working"); off a TTY that line is omitted and the output is pure, greppable text.

Two surfaces, one event vocabulary:
  * ``Stream``            — terminal: append-only lines (+ optional status line)
  * ``render_markdown_snapshot`` — Claude Desktop / MCP: a compact markdown block

Both are built from pure functions so they're fully testable without a terminal.
"""

from __future__ import annotations

import json
import os
from typing import Any, List, Optional, Tuple

# ---- ANSI (only ever applied when the sink is a real terminal) -------------
_C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "green": "\033[32m", "red": "\033[31m", "yellow": "\033[33m",
    "cyan": "\033[36m", "blue": "\033[34m", "grey": "\033[90m",
}
_CLR_LINE = "\r\033[2K"                              # erase the transient status line
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"                                 # a calm braille spinner
_SPIN_A = "|/-\\"                                    # ASCII spinner fallback
_NUM = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"

# Some IDE-embedded terminals (older Cursor/Codex builds, non-UTF-8 SSH, `TERM=dumb`)
# render exotic glyphs as tofu or double-width. We fold to a plain-ASCII set there so
# the stream reads identically everywhere. Circled numbers are handled in _tag().
_FOLD = str.maketrans({
    "⚒": "#", "✓": "v", "✗": "x", "·": "-", "⛔": "!", "⏸": "=",
    "⟲": "~", "→": "->", "…": "...", "⠋": "|", "—": "-", "–": "-",
})


def supports_unicode(out: Any) -> bool:
    """False when the sink clearly can't render our glyphs (non-UTF-8 encoding,
    TERM=dumb, or LOOPHOLE_ASCII set) — then we fall back to ASCII."""
    if os.environ.get("LOOPHOLE_ASCII"):
        return False
    if (os.environ.get("TERM") or "").lower() == "dumb":
        return False
    enc = (getattr(out, "encoding", None) or "").lower()
    return "utf" in enc if enc else True


def supports_color(out: Any) -> bool:
    """Color only on a real TTY, and never when NO_COLOR is set (informal std)."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    return out.isatty() if hasattr(out, "isatty") else False

# tool name -> a short human verb for the activity line
_VERB = {"write_file": "wrote", "edit_file": "edited", "read_file": "read",
         "run_shell": "ran", "apply_patch": "patched", "create_file": "wrote"}

# events that are pure bookkeeping — never worth a line
_MUTE = {"goal_created", "goal_status", "executor_network",
         "task_orphans_reconciled"}


def _payload(e: Any) -> dict:
    raw = e["payload"] if hasattr(e, "keys") and "payload" in e.keys() else (
        e.get("payload") if isinstance(e, dict) else None)
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _get(e: Any, key: str) -> Any:
    if isinstance(e, dict):
        return e.get(key)
    return e[key] if key in e.keys() else None


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())                     # collapse whitespace/newlines
    return s if len(s) <= n else s[: n - 1] + "…"


class Stream:
    """Turns the append-only event log into append-only milestone lines.

    ``consume(event)`` returns a finished line (str) for a milestone, or None to
    suppress. State (agent numbering, merge/round counts, retries) accumulates so
    the same task keeps the same ① and retries are labelled. Pure w.r.t. I/O.
    """

    def __init__(self, color: bool = False, width: int = 76,
                 ascii_only: bool = False) -> None:
        self.color = color
        self.width = width
        self.ascii = ascii_only
        self._num: dict = {}          # task_id -> agent-number glyph
        self._runs: dict = {}         # task_id -> how many times it has started
        self.merged = 0
        self.rejected = 0
        self.rounds = 0
        self.blocked = 0

    # -- glyph folding for terminals that can't render our Unicode --
    def fold(self, s: str) -> str:
        return s.translate(_FOLD) if self.ascii else s

    # -- colour helper --
    def _c(self, s: str, name: str) -> str:
        return (_C[name] + s + _C["reset"]) if self.color else s

    def _tag(self, task_id: Optional[str]) -> str:
        if not task_id:
            return " ·"
        if task_id not in self._num:
            i = len(self._num)
            self._num[task_id] = ("{})".format(i + 1) if self.ascii
                                  else (_NUM[i] if i < len(_NUM) else "({})".format(i + 1)))
        return " " + self._num[task_id]

    def _line(self, glyph: str, color: str, text: str) -> str:
        return self.fold("   {}  {}".format(self._c(glyph, color), text))

    def consume(self, e: Any) -> Optional[str]:
        kind = _get(e, "kind")
        if kind in _MUTE:
            return None
        p = _payload(e)
        tid = _get(e, "task_id")

        if kind == "task_created":
            return self._line(self._tag(tid).strip() or "·", "grey",
                              self._c("queued  " + _clip(p.get("description", ""), 52), "grey"))

        if kind == "task_status":
            if p.get("status") != "running":
                return None                          # done/failed/pending covered elsewhere
            self._runs[tid] = self._runs.get(tid, 0) + 1
            tag = self._tag(tid)
            if self._runs[tid] == 1:
                return self._line(tag.strip(), "cyan", "agent   → working…")
            return self._line(tag.strip(), "yellow",
                              "agent   → retry {}".format(self._runs[tid]))

        if kind == "agent_step":
            verb = _VERB.get(p.get("tool", ""), p.get("tool", "step"))
            note = _clip(p.get("note", ""), 48)
            # notes often already lead with the action ("wrote slugify.py") — don't
            # double it up ("wrote  wrote slugify.py").
            body = (self._c(note, "grey") if note.lower().startswith(verb.lower())
                    else "{:<7} {}".format(verb, self._c(note, "grey")))
            return self._line(self._tag(tid).strip(), "blue", body)

        if kind == "task_done":
            self.merged += 1
            return self._line("✓", "green",
                              self._c("merged  ", "green")
                              + _clip(p.get("summary", "candidate accepted"), 44)
                              + self._c("   [{} merged]".format(self.merged), "grey"))

        if kind == "task_failed":
            return self._line("✗", "red",
                              self._c("agent gave up  ", "red") + _clip(p.get("error", ""), 44))

        if kind == "merge_gate_reject":
            self.rejected += 1
            why = "; ".join((p.get("failures") or []) + (p.get("violations") or []))
            return self._line("✗", "red",
                              self._c("REJECT  ", "red")
                              + self._c("verifier blocked the merge: ", "bold")
                              + _clip(why or "verified red", 40))

        if kind == "write_glob_violation":
            self.blocked += 1
            return self._line("⛔", "red",
                              self._c("BLOCKED ", "red")
                              + "write outside the allowlist: "
                              + _clip("; ".join(p.get("violations", [])), 34))

        if kind == "soft_fail_closed":
            return self._line("⏸", "yellow",
                              self._c("PAUSED  ", "yellow")
                              + "handing to a human: " + _clip(p.get("reason", ""), 34))

        if kind == "merge_gate_reconcile":
            return self._line("⟲", "yellow",
                              "rolled back to the last verified-green commit")

        if kind == "verifier_bypasses":
            return self._line("!", "yellow", "adversary flagged a verifier bypass")

        if kind == "verify_run":
            self.rounds += 1
            score = int(p.get("score", 0))
            if p.get("passed"):
                return self._line("✓", "green",
                                  self._c("VERIFIED  all checks green", "green")
                                  + self._c("  · score {}".format(score), "grey"))
            return self._line("·", "grey",
                              self._c("gate    not green yet · score {}".format(score), "grey"))
        return None

    # -- the closing verdict line --
    def summary(self, status: str, elapsed_s: Optional[int] = None) -> str:
        ok = status == "done"
        head = (self._c("✓ VERIFIED DONE", "green") if ok
                else self._c("✗ NOT DONE", "red"))
        bits = ["{} round{}".format(self.rounds, "" if self.rounds == 1 else "s")]
        if elapsed_s is not None:
            bits.append("{}s".format(elapsed_s))
        bits.append("{} merged".format(self.merged))
        if self.rejected:
            bits.append("{} rejected before accept".format(self.rejected))
        if self.blocked:
            bits.append("{} cheat(s) blocked".format(self.blocked))
        return self.fold("\n   {}   {}\n   {}".format(
            head, self._c(" · ".join(bits), "grey"),
            self._c("done means the verifier passed — not that an agent said so.", "grey")))


# --------------------------------------------------------------------------- #
# live terminal loop                                                          #
# --------------------------------------------------------------------------- #
def stream_run(store: Any, goal_id: str, out: Any = None, interval: float = 0.4,
               run_callable=None) -> Any:
    """Live-render the milestone stream until the goal reaches a terminal state.

    If ``run_callable`` is given it is executed in a background thread (this is
    ``loophole run --watch``); otherwise an already-running goal is followed
    (``loophole watch``). Returns whatever ``run_callable`` returned (or None).
    """
    import sys
    import threading
    import time as _time
    from .contract import GoalContract

    out = out or sys.stdout
    tty = out.isatty() if hasattr(out, "isatty") else False
    color = supports_color(out)
    uni = supports_unicode(out)
    g0 = store.get_goal(goal_id)
    if not g0:
        out.write("no such goal: {}\n".format(goal_id))
        return None
    goal_text = GoalContract.from_json(g0["contract"]).goal
    t0 = g0["created_at"] if "created_at" in g0.keys() else None

    st = Stream(color=color, width=76, ascii_only=not uni)
    hammer = st.fold("⚒")
    header = ("\033[1m{}  loophole\033[0m".format(hammer) if color
              else "{}  loophole".format(hammer))
    out.write("\n{}  ·  {}\n\n".format(header, _clip(goal_text, 88)))
    out.flush()
    spinner = _SPIN if uni else _SPIN_A

    box: dict = {}
    if run_callable is not None:
        def _runner():
            try:
                box["result"] = run_callable()
            except BaseException as e:               # re-raised in caller after join
                box["error"] = e
        th = threading.Thread(target=_runner, daemon=True)
        th.start()
    else:
        th = None

    seen = 0          # events consumed so far (by position)
    spin = 0
    terminal = {"done", "failed", "paused"}

    def _clear_status():
        if tty:
            out.write(_CLR_LINE)

    def _flush_new():
        nonlocal seen
        evs = store.events(goal_id)
        new = evs[seen:]
        if new:
            _clear_status()
            for e in new:
                line = st.consume(e)
                if line is not None:
                    out.write(line + "\n")
            seen = len(evs)
            out.flush()

    def _status(g):
        nonlocal spin
        if not tty:               # in-place status line only on a real terminal
            return
        running = sum(1 for t in store.tasks_for_goal(goal_id) if t.status == "running")
        el = ""
        if t0 is not None:
            try:
                el = " · {}s".format(int(_now() - t0))
            except Exception:
                el = ""
        msg = st.fold("{} {} agent{} working · round {}{}".format(
            spinner[spin % len(spinner)], running, "" if running == 1 else "s",
            st.rounds, el))
        c0, c1 = (_C["grey"], _C["reset"]) if color else ("", "")
        out.write(_CLR_LINE + c0 + "   " + msg + c1)
        out.flush()
        spin += 1

    while True:
        g = store.get_goal(goal_id)
        _flush_new()
        alive = th.is_alive() if th is not None else (g and g["status"] not in terminal)
        if not alive:
            break
        _status(g)
        _time.sleep(interval)

    if th is not None:
        th.join()
    _flush_new()
    g = store.get_goal(goal_id)
    _clear_status()
    el = None
    if t0 is not None and g is not None:
        try:
            el = int((g["updated_at"] if "updated_at" in g.keys() else _now()) - t0)
        except Exception:
            el = None
    out.write(st.summary(g["status"] if g else "failed", el) + "\n\n")
    out.flush()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _now() -> float:
    # centralised so tests can monkeypatch; real value comes from the store's ts.
    import time as _t
    return _t.time()


# --------------------------------------------------------------------------- #
# Claude Desktop / MCP surface — a compact markdown snapshot                   #
# --------------------------------------------------------------------------- #
def render_markdown_snapshot(store: Any, goal_id: str, max_milestones: int = 12) -> str:
    """A compact markdown block suitable for Claude Desktop / an MCP tool result.

    No ANSI, no repaint — just the current state: the goal, a per-agent table, the
    most recent boundary/verify milestones, and the verdict. Call it periodically
    (or once at the end) to surface a run inside a chat surface."""
    from .contract import GoalContract
    g = store.get_goal(goal_id)
    if not g:
        return "_no such goal: {}_".format(goal_id)
    goal_text = GoalContract.from_json(g["contract"]).goal
    events = store.events(goal_id)
    tasks = store.tasks_for_goal(goal_id)

    st = Stream(color=False)
    milestones: List[str] = []
    for e in events:
        line = st.consume(e)
        if line is not None:
            milestones.append(line.strip())

    status = g["status"]
    badge = {"done": "✅ verified done", "failed": "❌ not done",
             "paused": "⏸️ paused for human", "running": "🔄 running"}.get(status, status)

    out: List[str] = []
    out.append("### ⚒ loophole — {}".format(_clip(goal_text, 80)))
    out.append("**{}**".format(badge))
    out.append("")
    if tasks:
        out.append("| # | task | state |")
        out.append("|---|------|-------|")
        icon = {"done": "✓ merged", "failed": "✗ failed", "running": "… working",
                "pending": "queued", "paused": "paused"}
        for i, t in enumerate(tasks):
            out.append("| {} | {} | {} |".format(
                _NUM[i] if i < len(_NUM) else i + 1,
                _clip(t.description, 44), icon.get(t.status, t.status)))
        out.append("")
    if milestones:
        out.append("**Milestones**")
        for m in milestones[-max_milestones:]:
            out.append("- {}".format(m))
        out.append("")
    verdict = "**Verdict:** {} · {} round(s) · {} merged".format(
        badge, st.rounds, st.merged)
    if st.rejected:
        verdict += " · {} rejected before accept".format(st.rejected)
    if st.blocked:
        verdict += " · {} cheat(s) blocked".format(st.blocked)
    out.append(verdict)
    out.append("")
    out.append("_done means the verifier passed — not that an agent said so._")
    return "\n".join(out)
