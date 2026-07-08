---
description: Run a goal through loophole with live progress
argument-hint: GOAL="..." VERIFY="..."
---

Run a coding goal through **loophole** and show its progress live. loophole — not you —
decides "done" (its verifier does). Requires the `loophole` MCP server registered in your
Codex config (see examples/agent-integration.md). Never fabricate progress; relay only what
the loophole tools return.

Steps:

1. Read the goal and verify command from `$ARGUMENTS` (named `GOAL="..."` / `VERIFY="..."`,
   or `<goal> || <verify>`). `VERIFY` is a shell command where exit 0 means done.

2. Call the `loophole_run` MCP tool with `{ "goal": GOAL, "verify": VERIFY }`. Its result text
   begins with ``Started run `goal-xxxx` `` — capture that goal id. Print the initial snapshot.

3. Poll: every ~4–5 seconds call `loophole_status` with `{ "goal_id": "goal-xxxx" }` and print
   the returned markdown snapshot, until the verdict is **done**, **failed**, or **paused**.

4. Print the final snapshot. On **paused**, note it needs input (`loophole resume <id>`); on
   **failed**, summarize the residual-risk report.
