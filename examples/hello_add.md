# Example: hello, add()

The simplest possible loophole run — verified end-to-end with a local Ollama model.

```bash
loophole run \
  "Create a file add.py containing a function add(a, b) that returns a + b." \
  --verify 'python3 -c "from add import add; assert add(2,3)==5; assert add(-1,1)==0; print(\"VERIFIED\")"' \
  --executor-model ollama:qwen3-coder:30b \
  --planner-model ollama:qwen3-coder:30b \
  --max-rounds 3 --max-parallel 1 \
  --workspace ./loophole-out
```

What happens:

1. **Plan** — the planner decomposes the goal into a task DAG (here: one task).
2. **Execute** — an executor agent works in its own git worktree, calls
   `write_file` to create `add.py`, then commits.
3. **Integrate** — the task branch merges into the integration HEAD (merge-train).
4. **Verify** — loophole does a *fresh checkout* of the merged candidate and runs
   the hard verifier. Exit 0 → the goal is DONE.
5. **Report** — a Residual-Risk Report states what was proven and what wasn't.

Key behaviors you can observe:

- If the agent *claims* "done" but never actually wrote a file, loophole rejects
  the false completion (no commit = not done).
- If the verifier never passes, loophole replans when it detects no progress
  (verifier-metric stall), and pauses rather than looping forever.
- A goal with **no verifier is rejected at submission** — you must define "done".
