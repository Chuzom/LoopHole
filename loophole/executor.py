"""Executor — a tool-using ReAct agent that completes ONE task.

The executor is not a text generator (council critique #3): it loops
think -> tool-call -> observe inside its sandboxed worktree until it declares the
task complete or hits the step budget. Returns an ExecResult the control loop
persists; the loop, not the executor, owns state and merging.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .provider import Provider, Msg, Completion
from .tools import Toolbelt, tool_schemas
from .state import Task


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


def execute_task(provider: Provider, task: Task, worktree: str,
                 max_steps: int = 12, shell_timeout: int = 120) -> ExecResult:
    belt = Toolbelt(worktree, timeout=shell_timeout)
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
    for step in range(max_steps):
        comp: Completion = provider.complete(msgs, tools=schemas, temperature=0.2)
        pt += comp.prompt_tokens
        ct += comp.completion_tokens

        if comp.tool_calls:
            # record the assistant turn, then execute each tool call
            msgs.append(Msg("assistant", comp.text or "(tool calls)"))
            for tc in comp.tool_calls:
                result = belt.dispatch(tc.name, tc.arguments)
                tools_used += 1
                msgs.append(Msg("tool", str(result)[:6000], name=tc.name,
                                tool_call_id=tc.id))
            continue

        text = comp.text or ""
        msgs.append(Msg("assistant", text))
        # C10 fix: require TASK_COMPLETE as an exact line, not a loose substring
        # (so quoted/"not TASK_COMPLETE" text can't accidentally finish the task).
        completed = any(line.strip() == "TASK_COMPLETE" or
                        line.strip().startswith("TASK_COMPLETE ")
                        for line in text.splitlines())
        if completed:
            # Reject a completion claim from an agent that never touched a tool:
            # it cannot have changed anything (council critique B — no fake "done").
            if tools_used == 0:
                msgs.append(Msg(
                    "user",
                    "You claimed completion but never called a tool, so nothing was "
                    "actually written. Use write_file/run_shell to do the work, THEN "
                    "reply TASK_COMPLETE."))
                continue
            summary = text.split("TASK_COMPLETE", 1)[1].strip()[:300] or "completed"
            return ExecResult(True, summary, step + 1, pt, ct)
        # nudge toward action if it stalled without tools or completion
        msgs.append(Msg("user", "Call a tool (e.g. write_file) to make progress, "
                                "then reply TASK_COMPLETE <summary> when done."))

    if tools_used == 0:
        return ExecResult(False, "made no tool calls in {} steps".format(max_steps),
                          max_steps, pt, ct)
    return ExecResult(False, "step budget exhausted ({} steps)".format(max_steps),
                      max_steps, pt, ct)
