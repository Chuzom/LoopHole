# loophole and GitHub Agentic Workflows: complementary, not competing

GitHub ships its own framework for running AI agents inside Actions —
[Agentic Workflows (gh-aw)](https://github.github.com/gh-aw/). It's the
single largest distribution surface in this whole space, and worth being
precise about: it solves a real, different problem than loophole does. This
is the plain answer to "why not just use gh-aw instead."

## What gh-aw actually secures

A gh-aw workflow is a markdown file with YAML frontmatter, compiled into a
hardened `.lock.yml` Actions workflow. Its safety model is about the
**agent's own runtime**, not the code it produces:

- The agent runs with a **read-only token** — it can inspect the repo but
  can't push or write directly.
- No secrets reach the agent's own inference step; those live in separate,
  downstream jobs.
- The agent's container runs **behind a network firewall**, reachable only
  to allowed destinations.
- Any action the agent wants to take (open an issue, comment, etc.) has to
  match a declared `safe-outputs` schema — a **gate on the shape of the
  proposed action**, checked before it's ever sent to GitHub's API.

That's a genuinely hard, important problem: a compromised or manipulated
agent reasoning process shouldn't be able to directly mutate your repo.
gh-aw's answer is to separate "the agent thinks" from "GitHub gets
mutated," with a schema-validated gate in between.

## What it doesn't do

None of that checks whether the **code the agent wrote actually works**.
`safe-outputs` validates that a proposed action is well-formed and of an
allowed type (a `create-issue` with the right shape, say) — it has no
concept of "run the test suite," "hit this health-check endpoint," or "did
this diff quietly delete the test that was failing." That's a different
layer, and it's the one loophole exists for: a Goal Contract, a HARD
verifier that's a real command, and a merge gate that re-runs it before
the change is accepted.

## Where loophole fits

Use gh-aw for what it's good at: running the agent itself somewhere safe,
with its blast radius contained. Use loophole for what it's good at:
deciding whether the pull request that agent opened is actually done —
the same acceptance gate whether the PR came from gh-aw, Claude Code,
Codex, aider, or a human.

A gh-aw workflow's compiled `.lock.yml` is, underneath, a normal GitHub
Actions workflow — which means loophole plugs in exactly the way it does
for any other agent-authored PR: a second, ordinary job on the same
`pull_request` event.

```yaml
# .github/workflows/loophole-gate.yml — runs alongside whatever
# gh-aw workflow opened the PR; doesn't need to know gh-aw exists.
on: [pull_request]
permissions:
  pull-requests: write
  checks: write
jobs:
  acceptance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Chuzom/loophole@v1
        with:
          contract: loophole.json
          comment: true
```

**Honesty note:** this recipe is grounded in gh-aw's real, documented
frontmatter/safe-outputs/firewall model (see the citation above), not a
guess — but it's a documented pattern, not something run end-to-end
against a live gh-aw-enabled repo in this session. If you try it and it
doesn't compose the way this describes, that's exactly the kind of gap
worth an issue — see [CONTRIBUTING.md](../CONTRIBUTING.md).
