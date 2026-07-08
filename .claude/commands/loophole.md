---
description: Run a goal through loophole (a verifier-gated agent swarm) with live progress
argument-hint: <goal> || <verify-command>
---

Run a coding goal through **loophole** and show its progress live. loophole — not you —
decides "done": a swarm of agents works in an isolated git worktree until the declared
verifier passes. Never fabricate progress; only relay what the loophole tools return.

Steps:

1. Split `$ARGUMENTS` on the first `||` into `<goal>` (left) and `<verify>` (right), trimming
   whitespace. `<verify>` is a shell command where exit 0 means done. If there is no `||`,
   ask the user for a verify command before continuing.

2. Call the `loophole_run` MCP tool with `{ "goal": <goal>, "verify": <verify> }`. Its result
   text begins with ``Started run `goal-xxxx` `` — capture that goal id. Print the initial
   markdown snapshot it returns.

3. Poll until the run finishes:
   - Wait ~4–5 seconds.
   - Call `loophole_status` with `{ "goal_id": "goal-xxxx" }`.
   - Print the returned markdown snapshot (goal, per-agent task table, milestones, verdict).
   - Stop when the verdict badge is **done**, **failed**, or **paused**.

4. Print the final snapshot as the outcome. If **paused**, tell the user it needs their input
   (e.g. a `locked`-tier human sign-off) and that they can resume with `loophole resume <id>`.
   If **failed**, summarize what the residual-risk report says went unproven.
