# loophole-agent-echo — example executor adapter

A minimal, working example of plugging **your own agent framework** into LoopHole as a
swarm worker. Copy this package, swap `EchoExecutor.run` for your framework's call,
rename `echo`, and publish.

## The whole integration (3 things)
1. An `Executor` subclass — `run(task, worktree, ctx=None) -> ExecResult` (see
   `loophole_agent_echo/plugin.py`). Do the work in `worktree`; optionally call
   `ctx.step(tool, note)` to stream steps into the FORGE.
2. A `register(api)` that maps a name → a factory.
3. The `loophole.executors` entry point in `pyproject.toml`.

## Try it
```bash
pip install -e ../..            # loophole core
pip install -e .                # this adapter
loophole executor list          # 'echo' now appears under framework adapters
loophole run "<goal>" --executor echo --verify "test -f notes.md"
```

## What you get for free
LoopHole wraps your executor with the **unchanged trust boundary**: OS sandbox, scoped
egress (`--executor-network` / `--executor-secret`), per-task write-allowlist, off-HEAD
merge gate, and the verifier. Your agent is untrusted; **the verifier decides 'done'.**
