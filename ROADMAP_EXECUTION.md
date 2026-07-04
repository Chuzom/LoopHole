# Loophole Execution Roadmap

This file tracks the near-term work that turns the product roadmap into shippable
tasks. The strategic roadmap remains in `ROADMAP.md`; this page is the working
checklist for the next execution pass.

## Immediate 5-step execution

| Step | Task | Status | Acceptance |
|---|---|---|---|
| 1 | Split CI into a core profile and a privileged sandbox/integration path. | Done | `LOOPHOLE_TEST_PROFILE=core pytest -q` passes locally; `.github/workflows/ci.yml` uses the core profile; `.github/workflows/sandbox.yml` remains the full bwrap proof. |
| 2 | Dogfood the GitHub Action/check/comment path on a real PR. | Done | A full `--comment` run can't complete live here (round-1 planning needs a reachable model; this repo has no model secrets in Actions). Proved the actually-uncertain part instead: `action-selftest.yml`'s new `comment-dogfood` job calls `gh.py`'s real REST functions directly against the live GitHub API on Chuzom/loophole#1 — a real sticky comment + a green `loophole / comment-dogfood` Check Run both landed on that PR. |
| 3 | Add secret-scrub regression coverage for JSON and PR comments. | Done | Writing the regression test surfaced a real gap: `--json`/`--json-file` had no scrub check at all (only `--comment` did). Fixed with `gh.redact_leaked_secrets()` applied once upstream of all three output surfaces; `tests/test_gh.py` covers a fake secret leaked via the goal string end to end. |
| 4 | Harden Action onboarding and release packaging. | Not started | README and generated workflow use `uses: Chuzom/loophole@v1`; the action self-test runs in CI. |
| 5 | Expand richer verifier adapters after the HTTP helper. | Done | HTTP health-check, coverage-threshold, LLM-judge rubric library, and mutation testing (Linux/bwrap only — mutmut's PTY use is blocked by macOS Seatbelt, verified live) all shipped with template contracts + tests. |

## Phase task plan

| Phase | Task | Status | Next action |
|---|---|---|---|
| P0 Foundation | Correct install command, exit codes, basic docs. | Done | Keep regression tests in CI. |
| P0 Foundation | Linux sandbox ergonomics. | Partly done | Keep full sandbox proof in `sandbox.yml`; add clearer local error messaging if bwrap/Seatbelt is unavailable. |
| P1 PR path | Stable JSON report. | Done locally | Validate schema output from an actual Action run. |
| P1 PR path | GitHub Action. | Mostly done | Dogfood on a real repository PR and capture failure modes. |
| P1 PR path | Sticky PR comment and Check Run. | Done locally | Run live against GitHub REST with `GITHUB_TOKEN`; verify update-in-place behavior. |
| P1 PR path | Secret scrub across public outputs. | Done | JSON, PR comment Markdown, and Check Run summaries all redact via one shared `gh.redact_leaked_secrets()` call upstream of all three; regression-tested in `tests/test_gh.py`. |
| P1 Onboarding | `loophole init` scaffolds contract plus Action workflow. | Mostly done | Switch generated CI to the Action one-liner once release packaging is ready. |
| P2 Executors | Codex, Claude Code, aider adapters. | Partly done | Prioritize Cursor/Composer and OpenHands only after P1 dogfood is stable. |
| P3 Contracts | CI inference and verifier registry. | Partly done | `--from-ci` inference and the local+remote registry (`registry add-source`) are done and tested; a loophole-operated hosted web index is explicitly deferred to Phase 4 (ROADMAP.md E3.2 scope note — needs a hosting/ops decision, not a CLI change). |
| P3 Verifiers | HTTP health, coverage, mutation, LLM rubric adapters. | Done | All four shipped with template contracts + tests. Mutation testing (`--verify-mutation`) is Linux/bwrap only — mutmut's PTY use is blocked by macOS Seatbelt (verified live via a real PermissionError), and mutmut is pinned below 3.x (3.3.1 segfaulted on every mutant in live testing). |
| P4 Hosted | GitHub App, dashboards, org policy. | Not started | Do not start until Phase 1 shows sustained PR-check usage. |
| P5 Category | Benchmarks and Goal Contract spec. | Not started | Start after P1/P2 proof points exist. |

## Current verification commands

```bash
LOOPHOLE_TEST_PROFILE=core pytest -q
pytest -q tests/test_contract.py
pytest -q
```

Use the first command for fast CI and local iteration. Use the full suite on a
host that permits localhost sockets and OS sandbox operations.
