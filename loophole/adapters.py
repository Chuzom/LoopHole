"""Built-in framework executor adapters (Phase 2).

Each adapter drives a full agent framework/harness as a swarm worker, inside
LoopHole's trust boundary (sandbox + scoped egress from Phase 1 + write-allowlist +
merge gate + verifier). The framework is untrusted; only the verifier grants 'done'.

- ClaudeCodeExecutor (Claude Code CLI) — `claude-code`.
- CodexExecutor (OpenAI Codex CLI) — `codex`. Verified against the real binary
  (codex-cli 0.80.0): `codex exec --json` was live-tested with both a ChatGPT-
  subscription account (captured its error-path JSONL) and a local Ollama model
  via `--oss` (captured a full success-path transcript) — see parse_codex_jsonl.

Each defaults its own network host + secret pass-through so `--executor <name>`
just works out of the box.
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


def parse_codex_jsonl(output: str) -> Tuple[List[str], str]:
    """Parse `codex exec --json`'s JSONL event stream into (steps, final_text).

    Verified against real `codex exec ... --json` transcripts (codex-cli 0.80.0):
    each line is one JSON event with a top-level ``type``. ``item.completed``
    events carry an ``item.type`` — ``agent_message`` items stream the reply
    token-by-token (chunks already carry their own leading whitespace, so
    ``"".join`` reconstructs the text); any other item type is presumed to be a
    tool/command action and surfaced as a step under its own type name (Codex's
    own vocabulary — this stays correct even if that vocabulary grows). A
    ``turn.failed``/``error`` event's ``message`` becomes the failure text.
    Codex also writes plain-text log lines to the same stream (observed live);
    non-JSON lines are skipped, same as Claude Code's parser."""
    steps: List[str] = []
    final_parts: List[str] = []
    error_msg = ""
    for line in (output or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        etype = ev.get("type")
        if etype == "item.completed":
            item = ev.get("item") or {}
            itype = item.get("type", "action")
            if itype == "agent_message":
                final_parts.append(str(item.get("text") or ""))
            else:
                steps.append(itype)
        elif etype in ("error", "turn.failed"):
            error_msg = str(ev.get("message")
                            or (ev.get("error") or {}).get("message") or error_msg)
    final = "".join(final_parts).strip() or error_msg
    return steps, final


class CodexExecutor(Executor):
    """Run the Codex CLI (`codex exec`) as each swarm worker.

    `codex exec` is already fully non-interactive by design — unlike top-level
    `codex`, it exposes no approval-policy flag to configure (there is nothing to
    configure: it never prompts). `--sandbox workspace-write` keeps Codex's OWN
    internal sandbox active as defense-in-depth even when loophole's outer OS
    sandbox is bypassed for a trusted run (see `trusted` below)."""

    DEFAULT_CMD = ("codex exec {task} --json --sandbox workspace-write "
                   "--skip-git-repo-check")

    def __init__(self, config: Optional[dict] = None):
        config = config or {}
        self.command = config.get("command") or self.DEFAULT_CMD
        self.shell_timeout = int(config.get("shell_timeout") or 600)
        # Codex needs its own API access — either an OPENAI_API_KEY or, for a
        # ChatGPT-subscription login, stored credentials under ~/.codex/ plus
        # OpenAI's auth hosts. Default both so `--executor codex` works out of
        # the box either way (still filesystem-confined; only these pass through).
        self.network_hosts = tuple(config.get("network_hosts") or ()) or \
            ("api.openai.com", "chatgpt.com", "auth.openai.com")
        self.pass_env = tuple(config.get("pass_env") or ()) or ("OPENAI_API_KEY",)
        # Codex, like Claude Code, is a KNOWN-TRUSTED framework agent: with
        # ChatGPT-subscription auth (the common case) it needs to read its stored
        # credentials under ~/.codex/, which loophole's OS sandbox would otherwise
        # block. Trusted by default; opt out with executor config
        # {"trusted": False} (or `loophole run --executor-sandboxed`) if you use
        # an API key instead and want Codex fully confined by loophole's sandbox
        # too (Codex's own --sandbox workspace-write still applies either way).
        self.trusted = bool(config.get("trusted", True))

    def run(self, task, worktree: str, ctx: Optional[ExecContext] = None) -> ExecResult:
        belt = Toolbelt(worktree, timeout=self.shell_timeout,
                        network_hosts=self.network_hosts, pass_env=self.pass_env,
                        trusted=self.trusted)
        cmd = self.command.replace("{task}", shlex.quote(task.description))
        if ctx is not None:
            ctx.step("codex", "start")
        res = belt.run_shell(cmd)
        steps, final = parse_codex_jsonl(res.output)
        if ctx is not None:
            for name in steps[:25]:
                ctx.step("codex", name)
        summary = (final or res.output or "").strip()[:300] or (
            "codex ok" if res.ok else "codex failed")
        from .executor import _log_network_denials
        _log_network_denials(belt, ctx)
        return ExecResult(ok=res.ok, summary=summary, steps=len(steps) or 1)


def register(api) -> None:
    """Register the built-in framework adapters (called by executors.load_executors)."""
    api.register_executor("claude-code", lambda cfg: ClaudeCodeExecutor(cfg))
    api.register_executor("codex", lambda cfg: CodexExecutor(cfg))
