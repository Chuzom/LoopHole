"""Built-in framework executor adapters (Phase 2).

Each adapter drives a full agent framework/harness as a swarm worker, inside
LoopHole's trust boundary (sandbox + scoped egress from Phase 1 + write-allowlist +
merge gate + verifier). The framework is untrusted; only the verifier grants 'done'.

Currently: ClaudeCodeExecutor (the Claude Code CLI). Registered under `claude-code`,
so `loophole run --executor claude-code` just works (it defaults its own network host
+ ANTHROPIC_API_KEY pass-through).
"""

from __future__ import annotations

import json
import shlex
from typing import List, Optional, Tuple

from .executor import ExecContext, ExecResult, Executor
from .tools import Toolbelt


def parse_stream_json(output: str) -> Tuple[List[str], str]:
    """Parse Claude Code `--output-format stream-json` into (tool_steps, final_text).

    Each line is a JSON event; we surface tool_use blocks as steps and the final
    result text. Non-JSON lines are ignored, so it degrades gracefully on plain output."""
    steps: List[str] = []
    final = ""
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        if etype == "assistant":
            for block in (ev.get("message", {}) or {}).get("content", []) or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    steps.append(str(block.get("name", "tool")))
        elif etype == "result":
            final = ev.get("result") or final
        elif etype == "text":
            final = ev.get("text") or final
    return steps, final


class ClaudeCodeExecutor(Executor):
    """Run the Claude Code CLI (`claude -p`) as each swarm worker. Streams its tool
    calls into the FORGE via `agent_step`; the verifier still owns 'done'."""

    # --permission-mode acceptEdits: in headless `-p` mode Claude Code otherwise only
    # PROPOSES file edits (nothing lands on disk). acceptEdits auto-applies edits — the
    # agent's job — without blanket-allowing arbitrary bash. The worktree is isolated
    # and the verifier still owns 'done'.
    DEFAULT_CMD = ("claude -p {task} --output-format stream-json --verbose "
                   "--permission-mode acceptEdits")

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.command = config.get("command") or self.DEFAULT_CMD
        self.shell_timeout = int(config.get("shell_timeout") or 600)
        # Claude Code needs its API + key — default them so `--executor claude-code`
        # works out of the box (still filesystem-confined; only THIS key passes through).
        self.network_hosts = tuple(config.get("network_hosts") or ()) or ("api.anthropic.com",)
        self.pass_env = tuple(config.get("pass_env") or ()) or ("ANTHROPIC_API_KEY",)
        # Claude Code is a KNOWN-TRUSTED framework agent — run it outside the OS
        # sandbox by DEFAULT so it can use your subscription login (keychain), which
        # the sandbox blocks. Opt out with executor config {"trusted": False} (or
        # `loophole run --executor-sandboxed`). The verifier still owns 'done'.
        self.trusted = bool(config.get("trusted", True))

    def run(self, task, worktree: str, ctx: Optional[ExecContext] = None) -> ExecResult:
        belt = Toolbelt(worktree, timeout=self.shell_timeout,
                        network_hosts=self.network_hosts, pass_env=self.pass_env,
                        trusted=self.trusted)
        cmd = self.command.replace("{task}", shlex.quote(task.description))
        if ctx is not None:
            ctx.step("claude-code", "start")
        res = belt.run_shell(cmd)
        steps, final = parse_stream_json(res.output)
        if ctx is not None:
            for name in steps[:25]:
                ctx.step("claude-code", name)
        summary = (final or res.output or "").strip()[:300] or (
            "claude-code ok" if res.ok else "claude-code failed")
        from .executor import _log_network_denials
        _log_network_denials(belt, ctx)
        return ExecResult(ok=res.ok, summary=summary, steps=len(steps) or 1)


def register(api) -> None:
    """Register the built-in framework adapters (called by executors.load_executors)."""
    api.register_executor("claude-code", lambda cfg: ClaudeCodeExecutor(cfg))
