<div align="center">

<img src="assets/loophole-flow.svg" alt="loophole — a swarm of agents that won't stop until the goal is provably done" width="820">

# loophole

**A swarm of AI agents that work on a goal until it's _provably_ done — and tells you exactly what it couldn't prove.**

**The acceptance layer for autonomous coding — "CI for AI agents." Bring your own agent; loophole is the trusted gate that decides what's _actually_ done.**

[![tests](https://img.shields.io/badge/tests-107%20passing-22c55e)](#)
[![python](https://img.shields.io/badge/python-3.9%2B-3776ab)](#)
[![providers](https://img.shields.io/badge/providers-Ollama%20%C2%B7%20Anthropic%20%C2%B7%20OpenAI-8b5cf6)](#)
[![license](https://img.shields.io/badge/license-MIT-64748b)](LICENSE)

</div>

---

## The problem

You give an AI agent a real task — *"build a REST API for a todo app, with tests"* — and three things go wrong:

1. **It quits too early.** One pass, a confident *"Done! ✅"*, and a half-working result.
2. **It lies about being finished.** Models are trained to please. *"All tests pass!"* — except it deleted the failing tests.
3. **It can't tell when it's actually done.** No goalpost, so it either stops at the first plausible output or loops forever.

`/loop`-style tools keep one agent grinding. But a single agent in a single context can't divide labor, can't hold a big task, and still grades its own homework.

## The idea

**loophole** turns a goal into a **task graph**, runs a **swarm of agents** across it in isolated git worktrees, and refuses to stop until an **external, falsifiable check** says the goal is met.

> The key move: **agents never decide they're done. A check does.**
>
> Your tests passing. A build succeeding. `curl` returning 200. If a goal can't be checked, loophole **asks a human** instead of guessing. It cannot be argued into calling unfinished work complete.

When the check fails, loophole re-plans, retries, and routes around dead ends — **until it genuinely passes or hits your budget.** Then it hands you a **Residual-Risk Report**: what it proved, and what it didn't.

## 60-second quickstart

```bash
git clone https://github.com/you/loophole && cd loophole
python -m venv .venv && source .venv/bin/activate
pip install -e .          # zero-config: works with local Ollama out of the box

# point it at a goal + a way to check "done":
loophole run "Create add.py with add(a,b) returning a+b" \
  --verify 'python3 -c "from add import add; assert add(2,3)==5; print(\"ok\")"' \
  --workspace ./out
```

```text
🎯 goal goal-3034039ba5f5
• round 1: planning
• round 1: executing 1 task(s)
• round 1: verify -> PASS (score 1000)
• goal -> done (all verifiers passed)

================ Residual-Risk Report ================
Outcome: DONE
What was VERIFIED:  [PASS] hard:python3 -c "from add import add; ..."
What was NOT proven: anything outside the verifier's scope.
=====================================================
```

That's the whole contract: **you define "done," loophole reaches it.**

## How it works

```
🎯 Goal ─▶ 🧭 Planner ─▶ ⚙️ Executors ─▶ ✅ Verifier ─▶ 🏁 Done
              ▲              (worktrees)        │
              └──── not done: re-plan / retry ──┘
```

| Stage | What happens |
|---|---|
| **Goal Contract** | Your goal + verifier(s). A goal with no way to check "done" is **rejected up front**. |
| **Planner** | Decomposes the goal into a dependency DAG; a critic pass attacks the plan before any work runs. |
| **Executors** | Tool-using agents (write/read files, run shell) work **in parallel**, each in its own git worktree, then merge serially. |
| **Verifier** | Runs your falsifiable check from a **fresh checkout** of the merged result. Only this grants completion. |
| **Loop control** | Measures progress by verifier metrics (not vibes), detects when it's stuck, and re-plans — with budget + round circuit-breakers. |

## Three kinds of "done"

| Verifier | Behavior | Use it for |
|---|---|---|
| **`hard`** — a command | exit 0 = done. The gold standard. | `pytest -q`, `npm test`, `make`, a health-check `curl` |
| **`soft`** — an LLM rubric | can only **veto**, never grant | subjective quality gates layered on top of a hard check |
| **`human`** — a checkpoint | loophole pauses and asks you | irreducibly subjective goals (prose, design) |

## Anti-reward-hacking (the part most tools skip)

Because the verifier *is* the goalpost, loophole defends it:

- **Verification boundary** — protected files (your tests, configs) are checked against the original commit; if an agent edits them, the run **fails**.
- **Test-count audit** — the suite can't silently shrink to make red turn green.
- **No fake "done"** — if an agent claims completion but changed nothing, it's rejected.
- **Secrets never reach verifiers** — your `ANTHROPIC_API_KEY` and friends are scrubbed from the subprocess environment.

## CLI

```bash
loophole init                                   # infer a starter loophole.json from the repo
loophole init --template refactor-frozen-tests  # or scaffold from a template
loophole run                                    # auto-loads ./loophole.json
loophole run "<goal>" --verify "pytest -q" [--workspace DIR]
loophole run --contract loophole.json --executor-command 'claude -p {task}'  # BYO agent
loophole run "<goal>" --protect "tests/**" --expect-test-delta 0   # lock the suite
loophole contract validate loophole.json        # validate / show a contract (path or URL)
loophole registry list                            # named, shareable acceptance specs
loophole registry add team-default ./loophole.json   # publish a spec; reuse by name
loophole run --contract team-default              # run a registry spec by name
loophole run "<goal>"                            # THE FORGE shows live by DEFAULT (interactive)
loophole run "<goal>" --no-watch                 # plain log (CI / when piping)
# models route via Chuzom by DEFAULT (planner=chuzom:simple, executor=chuzom:complex);
# set CHUZOM_URL to route through a live `chuzom-route` server, else local tier policy.
loophole run "<goal>" --executor-model ollama:qwen3-coder:30b   # or pin a model directly
loophole stats                                    # your value scorecard — verified, rejections, cheats blocked
loophole watch --demo                             # self-driving terminal swarm demo (no LLM)
loophole serve                                    # live web FLEET — all runs, click to drill in
loophole serve <goal-id>                          # live web Forge for one run
loophole demo                                     # the 30s 'can't-fake-done' demo (no LLM)
loophole audit <goal-id>                         # full audit trail (the trust artifact)
loophole runs                                    # list past runs
loophole estimate "<goal>" --max-rounds 10     # dry-run cost prediction
loophole status <goal-id>  ·  loophole resume <goal-id>  ·  loophole ls
```

## Providers

Provider-agnostic — pick per role (cheap executors, strong planner):

```bash
--planner-model anthropic:claude-sonnet-4-6 --executor-model ollama:qwen3-coder:30b
```

Default is **Ollama** (free, local, zero-config). Set `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` to use those. `pip install -e '.[anthropic]'` or `'.[openai]'` for the SDKs.

## Bring your own executor

loophole's value is the **trusted boundary around an _untrusted_ executor** — so the
executor is a pluggable backend. Use the built-in agent, or drive any external/
frontier coding agent as a black box; the sandbox, write-allowlist, merge gate, and
verifier are identical either way:

```bash
loophole run --contract loophole.json --executor-command 'claude -p {task}'
```

That's the bet: as models commoditize, *who wrote the code* matters less than
*whether it provably passes*. loophole is the neutral referee, not another coder.

## For teams — loophole as a CI acceptance gate

Let any agent open a PR; make loophole the gate that decides if it's done — in CI,
on neutral ground, with a reviewable audit trail:

- **`loophole.json` is acceptance-spec-as-code** — committed, reviewed, reusable.
  Scaffold with `loophole init` (it infers a starter from your repo) or
  `loophole init --template <name>`; share contracts by path or URL.
- **`loophole audit <run>`** renders every boundary decision (merge-gate rejections,
  write-allowlist violations, soft-judge escalations) with its reason — trust the
  result without reading every diff. `loophole runs` lists past runs.
- See **[`examples/ci_gate.md`](examples/ci_gate.md)** for a GitHub Actions gate, and
  **[`examples/cant_fake_done.py`](examples/cant_fake_done.py)** for the 30-second
  "it can't lie to me" demo.

## Honest status & safety

loophole is **v0.1**. Its promise is precise: it proves *"the candidate satisfies the declared contract under a trusted verifier boundary"* — **not** *"the goal is objectively achieved."* The Residual-Risk Report always says what went unchecked.

Agent-run shell commands are **OS-sandboxed** (macOS Seatbelt / Linux bubblewrap),
deny-by-default, network-denied, with provider secrets scrubbed — and **fail-closed**
if no sandbox is available. Still, treat goals and repos as you would any tool that
runs code, and prefer a disposable workspace. The architecture is adversarially
audited by a multi-model council; we publish our own findings.

## Roadmap

Shipped: OS sandbox for `run_shell`/verifiers · enforced per-task write-globs at
commit · per-merge re-verification (verified-green invariant) · fail-closed soft
judge · pluggable executors (bring-your-own-agent) · audit trail · shareable
contracts + templates.

Next (the acceptance-layer bet — *"CI for AI agents," bring-your-own-executor*):

- [ ] Hosted control-plane (run history, audit, policy, fleet dashboards)
- [ ] Verifier/contract registry (shareable acceptance specs)
- [ ] First-class executor adapters for frontier coding agents
- [ ] Richer verifier adapters (coverage, mutation testing)

## Contributing

Issues and PRs welcome. Run the suite with `pip install -e '.[dev]' && pytest`. The architecture was designed — and adversarially audited — by a multi-model council; that critique style is the project's default. Bring disagreement.

## License

MIT — see [LICENSE](LICENSE).
