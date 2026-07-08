"""Executors — the swappable WORKER that completes ONE task in a worktree.

VIS-1 (acceptance-layer bet): the agent that does the work is a pluggable backend.
LoopHole's value is the *trusted verifier boundary* around an *untrusted* executor,
so the executor can be anything — the built-in ReAct/provider loop, or an external
"bring-your-own" coding agent driven as a black box. The boundary (sandbox, write-
allowlist, merge gate, verifier) is unchanged regardless of backend.

All backends return an ExecResult the control loop persists; the loop, not the
executor, owns state, merging, and the decision of "done".
"""

from __future__ import annotations

import os
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional

from .provider import Provider, Msg, Completion
from .tools import Toolbelt, tool_schemas
from .state import Task
from .guarded import classify_guarded_action


EXECUTOR_SYSTEM = """You are an Executor agent in loophole. Complete ONE task by
calling tools. You operate inside a sandboxed workspace directory.

Available tools: write_file, read_file, list_dir, run_shell.

Work step by step:
- Inspect the workspace if needed (list_dir, read_file).
- Make the changes the task requires (write_file, run_shell).
- When the task is fully done, reply with the single line: TASK_COMPLETE
  followed by a one-sentence summary of what you did.

Only do THIS task. Do not attempt other tasks. Keep changes within your declared writes."""


@dataclass
class ExecResult:
    ok: bool
    summary: str
    steps: int
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class ExecContext:
    """Handed to an executor so a framework ADAPTER can stream its internal steps to
    the FORGE / audit log without importing core internals. All fields optional, so a
    black-box executor can ignore it and old 2-arg callers still work."""
    store: Any = None
    goal_id: Optional[str] = None
    task_id: Optional[str] = None

    def step(self, tool: str, note: str = "") -> None:
        """Record one internal agent step (a tool call, sub-agent, thought)."""
        if self.store is not None and self.goal_id is not None:
            try:
                self.store.log("agent_step", goal_id=self.goal_id, task_id=self.task_id,
                               payload={"tool": tool, "note": (note or "")[:200]})
            except Exception:
                pass

    def guarded(self, command: str, category: str) -> None:
        """Record a guarded side-effecting action (deploy/release/publish/push/purchase)
        for the Residual-Risk audit. Fires regardless of tier — the report decides how
        loudly to surface it."""
        if self.store is not None and self.goal_id is not None:
            try:
                self.store.log("guarded_action", goal_id=self.goal_id, task_id=self.task_id,
                               payload={"command": (command or "")[:200], "category": category})
            except Exception:
                pass


class Executor(ABC):
    """A swappable worker. Given a task and its sandboxed worktree, attempt the
    work and return an ExecResult. It NEVER decides 'done' — the verifier does.

    ``ctx`` (optional) lets a framework adapter emit ``agent_step`` events."""

    @abstractmethod
    def run(self, task: Task, worktree: str,
            ctx: Optional[ExecContext] = None) -> ExecResult:
        ...


class ReActExecutor(Executor):
    """The built-in tool-using ReAct agent (council critique #3: not a text
    generator — it loops think -> tool-call -> observe until it declares the task
    complete or hits the step budget)."""

    def __init__(self, provider: Provider, max_steps: int = 12,
                 shell_timeout: int = 120, allow_unsandboxed: bool = False):
        self.provider = provider
        self.max_steps = max_steps
        self.shell_timeout = shell_timeout
        self.allow_unsandboxed = allow_unsandboxed

    def run(self, task: Task, worktree: str,
            ctx: Optional[ExecContext] = None) -> ExecResult:
        belt = Toolbelt(worktree, timeout=self.shell_timeout,
                        allow_unsandboxed=self.allow_unsandboxed)
        schemas = tool_schemas()
        user = "TASK: {}\n".format(task.description)
        if task.reads:
            user += "You may read: {}\n".format(", ".join(task.reads))
        if task.writes:
            user += "You should write within: {}\n".format(", ".join(task.writes))
        msgs: List[Msg] = [Msg("system", EXECUTOR_SYSTEM), Msg("user", user)]

        pt = ct = 0
        summary = ""
        tools_used = 0
        for step in range(self.max_steps):
            comp: Completion = self.provider.complete(msgs, tools=schemas, temperature=0.2)
            pt += comp.prompt_tokens
            ct += comp.completion_tokens

            if comp.tool_calls:
                msgs.append(Msg("assistant", comp.text or "(tool calls)"))
                for tc in comp.tool_calls:
                    result = belt.dispatch(tc.name, tc.arguments)
                    tools_used += 1
                    if ctx is not None:
                        ctx.step(tc.name, str(result)[:80])   # stream to the FORGE
                        if tc.name == "run_shell":
                            _cmd = (tc.arguments or {}).get("command", "")
                            _cat = classify_guarded_action(_cmd)
                            if _cat:
                                ctx.guarded(_cmd, _cat)
                    msgs.append(Msg("tool", str(result)[:6000], name=tc.name,
                                    tool_call_id=tc.id))
                continue

            text = comp.text or ""
            msgs.append(Msg("assistant", text))
            # C10 fix: require TASK_COMPLETE as an exact line, not a loose substring.
            completed = any(line.strip() == "TASK_COMPLETE" or
                            line.strip().startswith("TASK_COMPLETE ")
                            for line in text.splitlines())
            if completed:
                # Reject a completion claim from an agent that never touched a tool.
                if tools_used == 0:
                    msgs.append(Msg(
                        "user",
                        "You claimed completion but never called a tool, so nothing was "
                        "actually written. Use write_file/run_shell to do the work, THEN "
                        "reply TASK_COMPLETE."))
                    continue
                summary = text.split("TASK_COMPLETE", 1)[1].strip()[:300] or "completed"
                return ExecResult(True, summary, step + 1, pt, ct)
            msgs.append(Msg("user", "Call a tool (e.g. write_file) to make progress, "
                                    "then reply TASK_COMPLETE <summary> when done."))

        if tools_used == 0:
            return ExecResult(False, "made no tool calls in {} steps".format(self.max_steps),
                              self.max_steps, pt, ct)
        return ExecResult(False, "step budget exhausted ({} steps)".format(self.max_steps),
                          self.max_steps, pt, ct)


class CommandExecutor(Executor):
    """Bring-your-own-executor: drive an EXTERNAL coding agent as a black box.

    The operator supplies a command template (e.g. ``claude -p {task}`` or any
    agent CLI). It runs inside the same OS sandbox as run_shell — network-denied,
    confined to the worktree — and the task text is shell-quoted into ``{task}``
    so it can't break out of the command. The external agent is fully untrusted;
    the verifier boundary remains the only authority on completion. The task is
    also written to ``TASK.md`` in the worktree for agents that prefer a file.
    """

    def __init__(self, command_template: str, shell_timeout: int = 600,
                 allow_unsandboxed: bool = False, network_hosts: tuple = (),
                 pass_env: tuple = ()):
        if "{task}" not in command_template:
            # Still valid (agent may read TASK.md), but warn-by-convention.
            pass
        # Phase 1: scoped egress + secret pass-through so an API-calling framework
        # agent can reach its API WITHOUT allow_unsandboxed.
        self.network_hosts = tuple(network_hosts)
        self.pass_env = tuple(pass_env)
        self.command_template = command_template
        self.shell_timeout = shell_timeout
        self.allow_unsandboxed = allow_unsandboxed

    def run(self, task: Task, worktree: str,
            ctx: Optional[ExecContext] = None) -> ExecResult:
        belt = Toolbelt(worktree, timeout=self.shell_timeout,
                        allow_unsandboxed=self.allow_unsandboxed,
                        network_hosts=self.network_hosts, pass_env=self.pass_env)
        # Make the task available as a file too (some agents take a prompt file).
        belt.write_file("TASK.md", "# Task\n\n{}\n".format(task.description))
        cmd = self.command_template.replace("{task}", shlex.quote(task.description))
        if ctx is not None:
            ctx.step("external-agent", cmd[:80])
            _cat = classify_guarded_action(cmd)
            if _cat:
                ctx.guarded(cmd, _cat)
        result = belt.run_shell(cmd)
        # TASK.md is orchestrator scaffolding for the agent to READ — not agent output.
        # Remove it after the run so it never counts against the per-task write-allowlist
        # (S5) or pollutes the merged result. (An external agent's REAL edits remain.)
        try:
            os.remove(os.path.join(worktree, "TASK.md"))
        except OSError:
            pass
        summary = ("external agent ok: " if result.ok else "external agent failed: ") \
            + str(result.output)[:240]
        _log_network_denials(belt, ctx)
        return ExecResult(ok=result.ok, summary=summary, steps=1)


def _log_network_denials(belt: Toolbelt, ctx: Optional["ExecContext"]) -> None:
    """Denied egress attempts are boundary decisions — put them in the audit trail."""
    denials = getattr(belt, "network_denials", None)   # tolerate Toolbelt stand-ins
    if denials and ctx is not None and getattr(ctx, "store", None):
        ctx.store.log("executor_network_denied", goal_id=ctx.goal_id,
                      task_id=ctx.task_id,
                      payload={"hosts": sorted(set(denials))})


def execute_task(provider: Provider, task: Task, worktree: str,
                 max_steps: int = 12, shell_timeout: int = 120,
                 allow_unsandboxed: bool = False) -> ExecResult:
    """Backwards-compatible shim: run the built-in ReAct executor."""
    return ReActExecutor(provider, max_steps=max_steps, shell_timeout=shell_timeout,
                         allow_unsandboxed=allow_unsandboxed).run(task, worktree)
