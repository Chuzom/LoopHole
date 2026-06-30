# Example: `loophole init` → `run`

Get a runnable contract without hand-writing flags. `init` inspects the repo
(filesystem only — no code execution, no model calls) and writes `loophole.json`.

```bash
$ loophole init
• detected pytest -> hard verifier 'pytest -q'
• inferred allowed_writes: mypkg/**, tests/**
• protected: .github/workflows/**, poetry.lock, LICENSE, loophole.json
wrote ./loophole.json
edit the "goal" field, then: loophole run --contract loophole.json
```

It infers what it safely can:

- **hard verifier** from the test runner (pytest / unittest / npm / go / cargo / make)
- **protected_paths** — CI config, lockfiles, `LICENSE`, and the contract file itself
- **allowed_writes** — narrowed to your source/test dirs (falls back to `**`)
- **expected_test_delta: 0** — the suite may not silently shrink

The one thing it never guesses is the **goal** — that stays a `TODO` you fill in:

```jsonc
{
  "goal": "TODO: describe the goal in one sentence (what 'done' means)",
  ...
}
```

Edit the goal, then run — the file is auto-discovered:

```bash
# explicit:
loophole run --contract loophole.json

# or, with ./loophole.json present, bare `run` auto-loads it:
loophole run
```

Running before you replace the `TODO` goal is refused, so you can't accidentally
launch an underspecified goal. An explicit `loophole run "real goal"` overrides
the file's goal for a one-off.
