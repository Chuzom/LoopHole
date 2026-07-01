"""loophole as an MCP server — describe a goal, run it, and watch progress from
INSIDE Claude Code / Claude Desktop / any MCP client, without leaving the session.

Deliberately zero-dependency: a small newline-delimited JSON-RPC 2.0 loop over
stdio (the MCP stdio transport), pure stdlib, Python 3.9+. It ships with loophole,
so `loophole mcp` just works — no extra SDK to install.

Tools exposed:
  * loophole_run    — start a goal (runs in the background); returns a goal id +
                      an initial progress snapshot.
  * loophole_status — the live progress of a run, as a markdown snapshot (the same
                      one Claude Desktop renders): agents, milestones, verdict.
  * loophole_list   — recent runs and their status.

The client (Claude Code) calls loophole_run, then polls loophole_status to show
progress in the session — so you see the swarm work without a terminal.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "loophole", "version": "0.1.0"}


# --------------------------------------------------------------------------- #
# run manager — persists for the server process's lifetime                    #
# --------------------------------------------------------------------------- #
class RunManager:
    """Starts goal runs in background threads and hands back their Store so a later
    status call (a separate JSON-RPC request to this same process) can read live
    progress."""

    def __init__(self) -> None:
        self._runs: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def start(self, goal: str, verify: str, model: Optional[str] = None,
              max_rounds: int = 6, workspace: Optional[str] = None) -> str:
        from .budget import Budget
        from .contract import GoalContract, Verifier, VerifierKind
        from .loop import LoopConfig, Roles, run_goal
        from .provider import default_model, make_provider
        from .state import Store

        model = model or default_model()            # auto: Chuzom if up, else local
        ws = os.path.realpath(workspace or tempfile.mkdtemp(prefix="loophole_mcp_"))
        _ensure_repo(ws)
        contract = GoalContract(
            goal=goal,
            verifiers=[Verifier(kind=VerifierKind.HARD, command=verify)],
            allowed_writes=["**"], max_rounds=max_rounds, timeout_seconds=120)
        store = Store(_default_db())
        gid = store.create_goal(contract.to_json(), ws)
        provider = make_provider(model)
        roles = Roles(planner=provider, executor=provider, critic=provider)
        cfg = LoopConfig(max_parallel=1, skip_plan_critique=True)

        def _go() -> None:
            try:
                run_goal(store, gid, contract, roles, Budget(), cfg, log=lambda _m: None)
            except Exception as e:                       # surface as a run failure
                try:
                    store.log("task_failed", goal_id=gid,
                              payload={"error": "run crashed: {}".format(e)[:200]})
                    store.set_goal_status(gid, "failed")
                except Exception:
                    pass

        th = threading.Thread(target=_go, daemon=True)
        th.start()
        with self._lock:
            self._runs[gid] = {"store": store, "thread": th, "workspace": ws}
        return gid

    def store_for(self, goal_id: str) -> Any:
        from .state import Store
        with self._lock:
            r = self._runs.get(goal_id)
        return r["store"] if r else Store(_default_db())

    def any_store(self) -> Any:
        from .state import Store
        with self._lock:
            for r in self._runs.values():
                return r["store"]
        return Store(_default_db())


def _default_db() -> str:
    home = os.path.join(os.path.expanduser("~"), ".loophole")
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "loophole.db")


def _ensure_repo(path: str) -> None:
    """loophole works in git worktrees, so the workspace must be a git repo."""
    os.makedirs(path, exist_ok=True)
    if os.path.isdir(os.path.join(path, ".git")):
        return
    ident = {"GIT_AUTHOR_NAME": "loophole", "GIT_AUTHOR_EMAIL": "loophole@localhost",
             "GIT_COMMITTER_NAME": "loophole", "GIT_COMMITTER_EMAIL": "loophole@localhost"}
    subprocess.run(["git", "init", "-q", path], check=True)
    subprocess.run(["git", "-C", path, "commit", "-q", "--allow-empty", "-m", "loophole init"],
                   env={**os.environ, **ident}, check=True)


# --------------------------------------------------------------------------- #
# tool definitions + handlers                                                 #
# --------------------------------------------------------------------------- #
def tool_specs() -> List[dict]:
    s = {"type": "string"}
    return [
        {"name": "loophole_run",
         "description": "Start a coding goal: a swarm of agents works in isolated git "
                        "worktrees until a verifier command PROVES the goal is done. "
                        "Returns a goal id and an initial progress snapshot; poll "
                        "loophole_status to watch it. 'done' means the verifier passed, "
                        "never that an agent claimed success.",
         "inputSchema": {"type": "object", "properties": {
             "goal": dict(s, description="What to build, in plain language."),
             "verify": dict(s, description="Shell command that exits 0 only when the "
                            "goal is met, e.g. 'pytest -q' or a python assertion."),
             "model": dict(s, description="provider:model. Default auto-selects: "
                           "'chuzom:auto' when Chuzom is running (cost-routed), else "
                           "'ollama:qwen3-coder:30b' (local, $0). Pass 'chuzom:complex' "
                           "to force Chuzom, or any provider:model."),
             "max_rounds": {"type": "integer", "description": "Max attempt rounds (default 6)."},
             "workspace": dict(s, description="Repo/dir to work in (default: a fresh temp repo)."),
         }, "required": ["goal", "verify"]}},
        {"name": "loophole_status",
         "description": "Live progress of a run as a markdown snapshot: the goal, a "
                        "per-agent table, recent milestones (merges, verifier "
                        "rejections, boundary blocks), and the verdict.",
         "inputSchema": {"type": "object", "properties": {
             "goal_id": dict(s, description="The id returned by loophole_run."),
         }, "required": ["goal_id"]}},
        {"name": "loophole_list",
         "description": "Recent loophole runs and their status.",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def call_tool(name: str, args: Dict[str, Any], runs: RunManager) -> dict:
    from .contract import GoalContract
    if name == "loophole_run":
        goal, verify = args.get("goal"), args.get("verify")
        if not goal or not verify:
            return dict(_text("loophole_run needs both 'goal' and 'verify'."), isError=True)
        from .provider import default_model
        model = args.get("model") or default_model()
        gid = runs.start(goal, verify, model=model,
                         max_rounds=int(args.get("max_rounds") or 6),
                         workspace=args.get("workspace"))
        routing = ("Chuzom (auto-detected — cost-routed)" if model.startswith("chuzom")
                   else model)
        from .stream import render_markdown_snapshot
        snap = render_markdown_snapshot(runs.store_for(gid), gid)
        return _text("Started run `{}` · model: {}\n\n{}\n\n_Call loophole_status with "
                     "this id to watch progress._".format(gid, routing, snap))
    if name == "loophole_status":
        gid = args.get("goal_id")
        if not gid:
            return dict(_text("loophole_status needs 'goal_id'."), isError=True)
        from .stream import render_markdown_snapshot
        return _text(render_markdown_snapshot(runs.store_for(gid), gid))
    if name == "loophole_list":
        store = runs.any_store()
        rows = store.list_goals()[:20]
        if not rows:
            return _text("No runs yet. Start one with loophole_run.")
        lines = ["| status | id | goal |", "|---|---|---|"]
        for g in rows:
            try:
                gt = GoalContract.from_json(g["contract"]).goal
            except Exception:
                gt = "?"
            lines.append("| {} | `{}` | {} |".format(g["status"], g["id"][:18], gt[:60]))
        return _text("\n".join(lines))
    return dict(_text("unknown tool: {}".format(name)), isError=True)


# --------------------------------------------------------------------------- #
# JSON-RPC dispatch (pure) + stdio serve loop                                 #
# --------------------------------------------------------------------------- #
def handle(msg: Dict[str, Any], runs: RunManager) -> Optional[dict]:
    """Map one JSON-RPC request to a response dict (or None for notifications)."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL_VERSION,
                   "capabilities": {"tools": {}},
                   "serverInfo": SERVER_INFO})
    if method in ("notifications/initialized", "initialized"):
        return None                                     # notification, no reply
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_specs()})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        try:
            return ok(call_tool(name, args, runs))
        except Exception as e:                          # never crash the transport
            return ok(dict(_text("tool error: {}".format(e)), isError=True))
    if mid is None:
        return None                                     # unknown notification
    return err(-32601, "method not found: {}".format(method))


def serve(stdin: Any = None, stdout: Any = None) -> None:
    """Run the stdio JSON-RPC loop until stdin closes."""
    import sys
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    runs = RunManager()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg, runs)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
