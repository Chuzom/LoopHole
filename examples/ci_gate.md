# Example: loophole as a CI acceptance gate for agent PRs

The acceptance-layer use case: let *any* agent open a PR, and make **loophole the
gate that decides whether it's actually done** — in CI, on neutral ground, with an
audit trail. The agent grades nothing; the contract does.

## The idea
1. An agent (Claude Code, Devin, an internal bot — or loophole's own executor)
   produces a change.
2. CI runs loophole against a committed `loophole.json` contract.
3. The job passes only if the verifier boundary passes. The run's audit trail
   (`loophole audit`) is the reviewable record.

## A minimal GitHub Actions gate

`.github/workflows/loophole-gate.yml`:

```yaml
name: loophole-gate
on: [pull_request]

jobs:
  acceptance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }

      # loophole sandboxes shell commands; bubblewrap is the Linux backend.
      - run: sudo apt-get update && sudo apt-get install -y bubblewrap
      - run: pip install loophole

      # Validate the committed contract, then run it against this PR's code.
      # Bring-your-own-executor: point --executor-command at whatever agent you use,
      # or omit it to use loophole's built-in executor.
      - run: loophole contract validate loophole.json
      - run: loophole run --contract loophole.json --workspace .

      # The trust artifact: attach the audit trail for reviewers.
      - if: always()
        run: loophole runs && loophole audit "$(loophole ls | head -1 | awk '{print $2}')"
```

## Why this is the wedge
- **Neutral referee.** A frontier lab grading its own agent has a conflict of
  interest; loophole doesn't — it only checks the contract.
- **Bring-your-own-executor.** Swap the agent without changing the gate
  (`--executor-command 'claude -p {task}'`). The boundary is identical.
- **Reviewable.** `loophole audit <id>` shows every boundary decision — merge-gate
  rejections, write-allowlist violations, soft-judge escalations — so a human can
  trust the result without reading every line.
- **Shareable contracts.** `loophole.json` is acceptance-spec-as-code; teams reuse
  templates (`loophole init --template …`) and pull contracts by URL.
