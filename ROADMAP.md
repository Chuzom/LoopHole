# loophole — Execution Plan: the popular "CI for agents"

> This is the detailed, sequenced plan derived from the product roadmap. It turns
> the strategy into implementation-ready work. **Phase 1 (get into the PR) is the
> make-or-break phase — everything before it clears the runway, everything after
> compounds it.** Do not build later phases before the phase before them shows a
> demand signal.

## How to read this
Each phase has **epics → tasks**. Every task lists: **Deliverable**, **Acceptance**
(how we know it's done — ideally a loophole contract could check it), **Touches**
(files/areas), **Size** (S ≤ 1d · M ≤ 3d · L ≤ 1wk · XL > 1wk), and **Depends on**.

## North-star metric
**Weekly PRs that carry a green/red loophole check.** Everything ladders to that.

| Phase | Unlocks | Leading metric |
|---|---|---|
| 0 Foundation | a first run never embarrasses | TTFR (time-to-first-verified-run) < 5 min |
| 1 Into the PR | loophole is *CI* | # repos with a loophole check / week |
| 2 Meet the agents | BYO is real | # distinct executors used |
| 3 Kill authoring friction | contracts compound | % runs on inferred/registry contracts |
| 4 Hosted control-plane | teams + revenue | installed orgs · weekly active repos |
| 5 Category leadership | loophole = the noun | benchmark reach · registry size |

---

## Phase 0 — Foundation & trust  (target: ~2 weeks)
*Nothing embarrasses a first-time user; the gate is production-honest.*

### E0.1 — Fix the install-command bug everywhere  ·  **P0, do first**
- **Task 0.1.1** Replace `pip install loophole` → `pip install loophole-agents`.
  - **Deliverable:** correct package name in `loophole/cli.py` `_CI_WORKFLOWS` (both github-actions and gitlab blocks), `examples/ci_gate.md`, `README.md` quickstart, and any docs.
  - **Acceptance:** `grep -rn "pip install loophole\b" .` returns nothing; a fresh `pip install loophole-agents` in a clean venv then running the scaffolded workflow succeeds.
  - **Touches:** `cli.py:_CI_WORKFLOWS`, `examples/ci_gate.md`, `README.md`. **Size:** S.

### E0.2 — Stable, documented exit codes
- **Task 0.2.1** Define and document CI exit codes: `0` = verified done, `1` = not done (paused/failed/budget), `2` = config/usage error (bad contract, no verifier, sandbox unavailable).
  - **Deliverable:** `run` maps outcomes to these codes deterministically; a `## Exit codes` section in the README CI docs.
  - **Acceptance:** table-driven test asserts each outcome → code; `loophole run` with a broken contract exits `2`, a failing verifier exits `1`.
  - **Touches:** `cli.py` run (currently `sys.exit(0 if status=="done" else 1)`), new `tests/test_exit_codes.py`. **Size:** S. **Depends on:** —

### E0.3 — Public repo + green badges
- **Task 0.3.1** Make `github.com/Chuzom/loophole` public; confirm ci/sandbox badges render green on the public page; social-preview image set (hero banner).
  - **Acceptance:** anonymous `curl -sI` of the repo returns 200; badges non-broken. **Size:** S (owner action).

### E0.4 — Linux-first sandbox ergonomics (CI runs on Linux)
- **Task 0.4.1** Verify bubblewrap path end-to-end on ubuntu-latest; make the "install bubblewrap" step unnecessary where possible (detect + clear error if missing) and documented where not.
  - **Acceptance:** a GitHub Actions job with no manual bwrap install either works or fails with a one-line actionable message. **Size:** M.

**Phase 0 exit criteria:** cold `pip install loophole-agents` → `loophole init` → `loophole run` reaches a verified run in < 5 min on both macOS and a Linux CI runner, with correct exit codes.

---

## Phase 1 — Get into the PR  (target: ~1–2 months)  ·  THE PHASE THAT MATTERS
*loophole becomes a status check + comment on agent PRs.*

### E1.1 — Machine-readable run output  (foundation for everything in P1)
- **Task 1.1.1** Add `loophole run --json` (and `--json-file PATH`) emitting a stable schema.
  - **Deliverable:** JSON with `{goal_id, status, exit_code, rounds, verified[], not_proven[], boundary_events[], cheats_blocked, verifier_rejections, budget:{usd,tokens}, duration_s, residual_risk_text}`.
  - **Acceptance:** `run --json` output validates against a committed JSON Schema; snapshot test on the demo run.
  - **Touches:** `cli.py` run, new `loophole/report.py:to_json(outcome, store, goal_id)` reusing `residual_risk_report`, `scorecard.run_scorecard`, `audit` events. **Size:** M. **Depends on:** E0.2.

### E1.2 — The GitHub Action  ·  **the adoption unlock**
- **Task 1.2.1** Create a composite/Docker Action at repo root (`action.yml`) + a `loophole-action` release channel.
  - **Deliverable:** `uses: Chuzom/loophole@v1` with inputs `contract` (default `loophole.json`), `executor`, `executor-command`, `comment` (bool), `fail-on` (`not-done`|`never`). Bundles python setup, bubblewrap, pip install pinned version, caching.
  - **Acceptance:** an example repo's PR triggers the Action and produces a Check Run; the action's own repo has a green "self-test" workflow using it.
  - **Touches:** new `action.yml`, `.github/actions/` or a thin wrapper repo, docs. **Size:** L. **Depends on:** E1.1.
- **Task 1.2.2** Publish to the GitHub Marketplace (listing, branding = the mascot, categories: CI, code-review).
  - **Acceptance:** discoverable on the Marketplace; README shows the `uses:` one-liner. **Size:** S. **Depends on:** 1.2.1.

### E1.3 — Native PR feedback  ·  **the visible signal**
- **Task 1.3.1** `--comment` mode: post the Residual-Risk Report + scorecard as a PR comment (upsert a single sticky comment, keyed by a marker, so re-runs edit in place).
  - **Deliverable:** Markdown comment with the verdict, "N cheats blocked", a collapsible audit trail, and next-steps.
  - **Acceptance:** on a test PR, exactly one loophole comment appears and updates on re-run; renders correctly with the boundary-decision glyphs.
  - **Touches:** new `loophole/gh.py` (GitHub REST via token from `GITHUB_TOKEN`), `cli.py` run `--comment`, reuse `report.py`/`audit.py` renderers. **Size:** M. **Depends on:** E1.1.
- **Task 1.3.2** GitHub **Check Run** with pass/fail conclusion + summary + annotations (e.g., annotate the protected file an agent tried to edit).
  - **Acceptance:** the PR shows a "loophole / acceptance" check that's red on reward-hacking, green on verified done, with a summary panel. **Size:** M. **Depends on:** 1.3.1.
- **Task 1.3.3** Security: never leak secrets into comments; the env-scrub already covers verifiers — assert the comment/JSON paths are scrubbed too.
  - **Acceptance:** a run with `ANTHROPIC_API_KEY` set produces a comment/JSON containing no secret-shaped strings (regression test). **Size:** S.

### E1.4 — One-command onboarding
- **Task 1.4.1** Upgrade `loophole init`: detect test runner/build (pytest/npm/go/make) and scaffold **both** a real `loophole.json` (narrowed `allowed_writes`, protected `tests/**`, inferred verifier) **and** the workflow that uses the Action (not the raw pip recipe).
  - **Acceptance:** in a fresh pytest repo, `loophole init` writes a contract whose verifier is the repo's real test command and a workflow using `uses: Chuzom/loophole@v1`; `loophole contract validate` passes.
  - **Touches:** `cli.py init`, `initializer.py` (already infers writes/protected), `_CI_WORKFLOWS` → Action-based template. **Size:** M. **Depends on:** 1.2.1.

**Phase 1 exit criteria:** a developer adds one `uses:` block (or runs `loophole init`), opens a PR, and gets a red check + a comment that catches a reward-hacking attempt — then green after the fix. **Recruit 3–5 design-partner teams here** (sharpest ICP: platform/infra teams clearing lint/test debt with agents).

---

## Phase 2 — Meet the agents where they are  (target: ~1–2 months, overlaps P1)
*"Bring your own agent" is true for the agents people actually run.*

### E2.1 — First-class executor adapters
- One `Executor` subclass + entry-point per agent, behind the identical boundary. Priority order by demand: **Cursor/Composer, Codex CLI, aider, OpenHands, Devin**. (Plugin system + `examples/adapter_package/` already exist.)
  - **Acceptance (per adapter):** `loophole run --executor <name>` drives the agent as a black box on the demo goal and reaches verified done; streams tool calls into the run view where the agent supports it; `test_<name>_adapter.py` green.
  - **Size:** M each.
### E2.2 — Compatibility matrix
- **Deliverable:** README table (agent × streams-tool-calls / needs-network / trusted-mode / status). **Acceptance:** every shipped adapter has a row; a `test_docs` check keeps it in sync with `loophole executor list`. **Size:** S.

**Phase 2 exit criteria:** a team can point loophole at their existing agent in one flag and see it in the compatibility matrix.

---

## Phase 3 — Kill authoring friction + network effects  (target: ~2–3 months)
*Writing the acceptance spec stops being the hard part; contracts compound.*

### E3.1 — Contract inference from existing CI
- Parse the repo's `.github/workflows/*.yml` (and common configs) to extract the test/build/lint commands most repos already encode as "done", and generate the starter contract from them.
  - **Acceptance:** on a repo with a pytest CI, `loophole init --from-ci` produces a contract whose hard verifier matches the CI's test step; measured on a corpus of N OSS repos. **Size:** L.
### E3.2 — Public contract/verifier registry (web index)
- Build on the existing local+remote index: a browsable web index + `loophole run --contract <name>@<ver>`, publish/pull flows, and provenance.
  - **Acceptance:** a contract published by team A is runnable by team B via name; the index lists it. **Size:** L.
  - **Scope note (2026-07-04):** the *pull* side of this already exists and is tested —
    `loophole registry add-source <url>` points at any JSON manifest (name → contract
    URL or inline contract), and `resolve()`/`remote_entries()` merge it into
    name-based resolution (`registry.py`, covered by `tests/test_registry.py`'s
    remote-index tests). That alone satisfies "team A publishes, team B runs it by
    name" today — team A just hosts a manifest wherever they already host files
    (GitHub raw, a gist, S3, an internal server) and shares the URL.
    What's genuinely missing — a canonical **loophole-operated** index (so
    users don't need to *find* a manifest URL first), a browsable web UI,
    `name@version` resolution, and a hosted publish flow — all require an
    actual hosting decision (a domain, an operator, an abuse/namespace policy,
    an ongoing cost owner). That's Phase 4 hosted-control-plane territory, not
    a CLI feature this repo can ship unilaterally, and the same "verify before
    building" evidence gathered for the Devin/Cursor/OpenHands executor scope
    calls applies here: don't stand up speculative hosted infra before Phase 1
    shows the pull-based self-hosted path is actually the bottleneck.
    **Decision:** defer the hosted index to Phase 4 (see E4.1); in the
    meantime, document `add-source` as the E3.2 answer for cross-team sharing.
### E3.3 — Richer verifier adapters
- Coverage threshold, mutation testing, HTTP/health-check helper, LLM-judge rubric library (module SDK already supports graded verifiers).
  - **Acceptance:** each ships with a template contract + test. **Size:** M each.

**Phase 3 exit criteria:** the median new user runs a *good* contract they didn't hand-author (inferred or from the registry).

---

## Phase 4 — Hosted control-plane  (target: ~3–6 months — only after P1 traction)
*Teams that won't self-host get history, policy, dashboards; loophole gets revenue.*

### E4.1 — GitHub App (org-wide install, managed checks, central run history/audit).  **Size:** XL.
### E4.2 — Dashboards: fleet view, cheats-blocked-over-time, per-agent verified-done rate (reuse the Chuzom verdict-feedback data already emitted).  **Size:** L.
### E4.3 — Org policy: required contracts, protected-path defaults, central budget ceilings.  **Size:** L.

**Gate:** do not start Phase 4 until Phase 1 shows sustained week-over-week growth in PRs-with-a-check. Premature hosted infra is the classic scale trap.

---

## Phase 5 — Category leadership  (ongoing)
- **E5.1 Public "can't-fake-done" benchmark:** run popular agents against adversarial-verifier tasks; publish who reward-hacks and how often. Press-worthy, screenshot-able, cements the neutral-referee brand.
- **E5.2 Routing-quality leaderboard:** surface the loophole↔Chuzom ground-truth verdicts publicly.
- **E5.3 Standardize the Goal Contract** as a spec others can implement — the moat is the format + trust brand, not the code.

---

## Cross-cutting (every phase)
- **Testing:** each feature ships with tests; keep the suite green (currently 242 passing). Security-sensitive paths (comments/JSON, network) get a scrub regression test.
- **Docs:** README stays honest and demo-first; each new surface gets a short doc + example.
- **Release:** Trusted-Publishing to PyPI on tags (already wired); Action versioned with a moving `v1` tag.
- **Dogfood:** loophole gates its own PRs with its own Action (the ultimate credibility demo).

## Do NOT do
- Compete on code generation (ride model commoditization; executor stays pluggable).
- Build the hosted dashboard before P1 demand.
- Over-invest in the local FORGE/web UI — the PR check drives adoption, not a local UI.
- Let marketing outrun the product's honesty; credibility *is* the product.

## Riskiest assumption to de-risk early
Teams will let agents open PRs *and* trust an automated gate to accept them. De-risk with 3–5 design partners during Phase 1; let their real usage shape Phases 2–3.
