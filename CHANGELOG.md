# Changelog

All notable changes to loophole are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/) once a version reaches 1.0.

## [Unreleased]

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
