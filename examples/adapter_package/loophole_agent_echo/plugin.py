"""Minimal example: plug an agent framework into LoopHole as a swarm worker.

The whole contract is three things:
  1. Implement ``Executor.run(task, worktree, ctx=None) -> ExecResult`` — do the work
     inside ``worktree`` (that's the task's isolated git checkout). Optionally call
     ``ctx.step(tool, note)`` to stream your framework's steps into the FORGE.
  2. Register a factory ``(config: dict) -> Executor`` under a name in ``register``.
  3. Expose ``register`` via the ``loophole.executors`` entry point (see pyproject.toml).

Then ``loophole run --executor echo`` uses it. LoopHole's trust boundary — OS sandbox,
scoped egress (``--executor-network`` / ``--executor-secret``), write-allowlist, merge
gate, verifier — wraps your executor unchanged. No adapter can grant 'done'; the
verifier decides.

Replace the body of ``EchoExecutor.run`` with your framework's call to make a real
adapter (drive Agno, a Hermes harness, an in-house agent, …).
"""

from __future__ import annotations

import os

from loophole.executor import ExecResult, Executor


class EchoExecutor(Executor):
    """Stand-in for a real framework agent: writes the task text to notes.md and
    reports a single step. A real adapter would call its framework here."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def run(self, task, worktree: str, ctx=None) -> ExecResult:
        if ctx is not None:
            ctx.step("echo", (task.description or "")[:60])   # -> FORGE agent_step
        with open(os.path.join(worktree, "notes.md"), "w", encoding="utf-8") as f:
            f.write("# task\n\n{}\n".format(task.description))
        return ExecResult(ok=True, summary="echoed task to notes.md", steps=1)


def register(api) -> None:
    api.register_executor("echo", lambda cfg: EchoExecutor(cfg))
