# loophole

> A swarm of agents that work toward a goal until an acceptance contract passes — and tells you exactly what it couldn't prove.

loophole decomposes a high-level goal into a task DAG, executes the tasks with
tool-using agents, and keeps iterating until a **falsifiable verifier** confirms
the work satisfies a declared **Goal Contract**. It never lets an agent decide
"I'm done" — an external, trusted check does.

## The core idea

The hardest problem in "run until done" systems is the **termination oracle**.
If an LLM judges its own completion, it will lie (sycophancy + reward hacking).
So loophole inverts the design: **verification is a first-class citizen.**

- **`hard` verifiers** (command exit-code, e.g. `pytest -q`) — *grant* completion.
- **`soft` verifiers** (LLM rubric) — can only *veto*, never grant, when a hard verifier exists.
- **`human` verifiers** (checkpoint) — for irreducibly subjective goals, loophole *pauses and asks*.

A goal with **no verifier is rejected at submission.** If you can't define "done,"
loophole will ask you — it won't guess.

## Architecture (council-ratified)

```
Goal Contract → Planner → plan_critic → Scheduler → Executors (worktrees) → Integration → Verifier
      ↑                                                                                        │
      └──────────────────── replan(failures) ◀── stuckness score ◀────────────────────────────┘
```

Key decisions:

| Concern | Decision |
|---|---|
| Termination | External falsifiable verifier; goal rejected if none declared |
| Verifier integrity | Verification Boundary — protected paths, test-count audit, fresh-checkout |
| State | SQLite WAL; tables are source of truth, `events` is audit log |
| Parallelism | Per-task git worktree from dependency-closure commit; serial merge-train |
| Execution | Tool-using ReAct agents, not text generators |
| Loop detection | Stuckness score from verifier-distance + code-state recurrence |
| Progress | Measured by verifier metrics, never task-completion count |
| Cost | Budget object; 80% → auto-downgrade, 100% → pause |
| Honesty | Residual-Risk Report at completion |

## Install

```bash
pip install -e .          # core (Ollama provider works out of the box)
pip install -e '.[anthropic]'   # + Anthropic
pip install -e '.[openai]'      # + OpenAI
pip install -e '.[dev]'         # + test deps
```

## CLI

```bash
loophole run "Build a FastAPI todo service" --verify "pytest -q" --workspace ./out
loophole run "..." --planner-model llama3 --executor-model llama3 --max-parallel 4
loophole estimate "..." --verify "pytest -q"   # dry-run cost prediction
loophole resume <goal-id>                       # continue after crash/pause
loophole status <goal-id>                        # task DAG + verifier status
loophole ls                                      # all goals
```

## Provider config

loophole reads `~/.loophole/config.toml` (optional). Default provider is Ollama
(`http://localhost:11434`), which is free and local. Set `ANTHROPIC_API_KEY` or
`OPENAI_API_KEY` to use those providers.

## Status

v0.1 — runnable scaffold. The load-bearing pieces (Goal Contract, Verification
Boundary, DAG validation, commit-graph integration, budget, residual-risk report)
are implemented. See `examples/` for a working trivial goal.

## License

MIT
