# Changelog

All notable changes to loophole are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) once a version reaches 1.0.

## [Unreleased]

### Added
- **Privilege tiers**: choose once at mission start how much freedom the swarm gets —
  `--privileges full|guarded|locked` (or an interactive prompt when a terminal is
  attached), also a `privilege_tier` field on the Goal Contract. `guarded` (default)
  equals the historical posture, so it is non-breaking; `locked` denies network,
  confines reads, and routes completion to a human sign-off.
- **Guarded-action audit**: side-effecting/irreversible commands the swarm runs
  (`git push`, `npm publish`/`twine upload`, `gh release`, `docker push`,
  `kubectl/terraform apply`, cloud deploys, purchases) are detected and recorded as
  `guarded_action` events, surfaced under "Guarded actions (audit)" in the
  Residual-Risk Report and in `loophole audit`.
- **Agent integration**: a `/loophole` slash-command for Claude Code and Codex that
  drives the MCP tools (`loophole_run` → poll `loophole_status`) to show live ASCII
  progress inside an agent chat. Templates in `examples/agents/` + an
  `examples/agent-integration.md` guide.
- **Fast structural-deadlock detection**: when no single task's merge can pass the hard
  verifier(s), HEAD never advances — previously the run churned to `max_rounds` (up to
  25) before failing generically. It now emits an advisory after 2 no-merge rounds and
  PAUSES with an actionable diagnostic after 5 ("no single task's changes pass the hard
  verifier(s) alone … bundle each source file with its own test, or scope the verifier so
  a partial merge can pass"). Catches the case degenerate-plan detection misses — a
  planner that keeps re-splitting the work into structurally different (but still
  co-dependent) tasks. The R3 merge-gate invariant is unchanged.

### Changed
- The `plan critic rejected` log line now includes its round number, so a rejection
  followed by the next round's re-plan+execute can't be misread as "a rejected plan ran".

### Fixed
- A human checkpoint with no human reachable (EOF on a non-interactive stdin) now
  PAUSES fail-closed instead of aborting the run or silently completing.

## [0.1.1] — 2026-07-07

### Security
- Routed GitHub Action inputs through `env:` and quoted shell variables to close a shell-injection vector.
- Added `SECURITY.md` with responsible-disclosure guidance via GitHub private advisories.

### Fixed
- Replaced the hardcoded personal MCP command path in `.mcp.json` with portable `loophole` PATH resolution.
- Corrected the README tests badge from `353 passing` to `353 total` and updated the badge-invariant test.

### Changed
- Updated the action self-test job to install the checked-out package and run report-only against the current action plumbing.

### Documentation
- Documented Ollama installation, model pulling, and the hosted-provider quickstart path.
- Added a callout that `claude-code` and `codex` executors run unsandboxed by default and can be re-confined with `--executor-sandboxed`.

## [0.1.0] — initial public release

The core acceptance-layer architecture, ratified by a multi-model council
review and hardened across two subsequent audit passes.

### Added

- **Goal Contract**: HARD (real shell command), SOFT (LLM-judge rubric that
  can only veto, never grant), and HUMAN (checkpoint) verifier kinds; a
  contract that admits no verifier at all is rejected outright.
- **Sandboxed execution**: agent swarm runs in git-worktree-isolated
  sandboxes — Seatbelt on macOS, bubblewrap on Linux — deny-by-default,
  network-denied unless a verifier or executor explicitly opts in, with
  secrets scrubbed from the subprocess environment and scoped egress
  (host-allowlisted proxy on macOS).
- **Merge gate**: every candidate is re-verified before its change is
  accepted, closing the "agent edits its own tests to fake a pass" gap that
  a single upfront check can't catch.
- **Executor SDK**: bring-your-own-agent via a pluggable `Executor`
  interface, plus first-party adapters for Claude Code, Codex CLI, and
  aider (each with a documented trusted/sandboxed posture and verified
  invocation flags).
- **Verifier library**: `--verify-http` (health-check), `--verify-coverage`
  (pytest-cov threshold), `--verify-rubric` (bundled LLM-judge rubrics —
  stub detection, hardcoded secrets, silent error swallowing, goal-scope
  drift), each shipping with a template contract and end-to-end tests.
- **Contract registry**: bundled starter templates, a local named registry,
  and remote index sources (`registry add-source <url>`) so a contract
  published by one team is runnable by another via name.
- **`loophole init`**: infers a starter contract from the repo, including
  `--from-ci` (parses the repo's own GitHub Actions workflows as ground
  truth) and `--template`.
- **GitHub Action** (`Chuzom/loophole@v1`) with a stable JSON result schema,
  a sticky PR comment, and an annotated Check Run — plus defense-in-depth
  secret scrubbing applied once, upstream of every output surface
  (`--json`, `--json-file`, and the PR comment/Check Run alike).
- **CLI surface**: `run`, `init`, `contract`, `registry`, `executor`,
  `stats`, `audit`, `runs`, `status`, `resume`, `watch`, `serve`,
  `estimate`, `demo`, `mcp` — stable, documented exit codes (`0` verified
  done, `1` not done, `2` usage error).
- **Value scorecard**: per-run metrics (verifier rejections, cheats
  blocked) and a `loophole stats` aggregate view.
- **Chuzom-routed models** by default, with verifier verdicts fed back as
  ground-truth routing-quality signal.
- **Hosted-free control-plane views**: a terminal FORGE dashboard and a
  local `loophole serve` fleet view — both self-hosted, no external
  service required.

[Unreleased]: https://github.com/Chuzom/loophole/compare/v0.1.0...main
[0.1.0]: https://github.com/Chuzom/loophole/releases/tag/v0.1.0
