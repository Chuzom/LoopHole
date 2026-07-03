# Loophole Execution Roadmap

This file tracks the near-term work that turns the product roadmap into shippable
tasks. The strategic roadmap remains in `ROADMAP.md`; this page is the working
checklist for the next execution pass.

## Immediate 5-step execution

| Step | Task | Status | Acceptance |
|---|---|---|---|
| 1 | Split CI into a core profile and a privileged sandbox/integration path. | Done | `LOOPHOLE_TEST_PROFILE=core pytest -q` passes locally; `.github/workflows/ci.yml` uses the core profile; `.github/workflows/sandbox.yml` remains the full bwrap proof. |
| 2 | Dogfood the GitHub Action/check/comment path on a real PR. | Not started | A real PR gets one sticky Loophole comment and a red/green check run from `GITHUB_TOKEN`. |
| 3 | Add secret-scrub regression coverage for JSON and PR comments. | Not started | A run with fake secret env vars emits no secret-shaped values in JSON, comments, or check summaries. |
| 4 | Harden Action onboarding and release packaging. | Not started | README and generated workflow use `uses: Chuzom/loophole@v1`; the action self-test runs in CI. |
| 5 | Expand richer verifier adapters after the HTTP helper. | Partly done | HTTP health-check helper is covered; next templates cover coverage threshold, mutation testing, and reusable LLM rubric verifiers. |

## Phase task plan

| Phase | Task | Status | Next action |
|---|---|---|---|
| P0 Foundation | Correct install command, exit codes, basic docs. | Done | Keep regression tests in CI. |
| P0 Foundation | Linux sandbox ergonomics. | Partly done | Keep full sandbox proof in `sandbox.yml`; add clearer local error messaging if bwrap/Seatbelt is unavailable. |
| P1 PR path | Stable JSON report. | Done locally | Validate schema output from an actual Action run. |
| P1 PR path | GitHub Action. | Mostly done | Dogfood on a real repository PR and capture failure modes. |
| P1 PR path | Sticky PR comment and Check Run. | Done locally | Run live against GitHub REST with `GITHUB_TOKEN`; verify update-in-place behavior. |
| P1 PR path | Secret scrub across public outputs. | Not started | Add tests for JSON, PR comment Markdown, and Check Run summaries. |
| P1 Onboarding | `loophole init` scaffolds contract plus Action workflow. | Mostly done | Switch generated CI to the Action one-liner once release packaging is ready. |
| P2 Executors | Codex, Claude Code, aider adapters. | Partly done | Prioritize Cursor/Composer and OpenHands only after P1 dogfood is stable. |
| P3 Contracts | CI inference and verifier registry. | Partly done | Keep local registry tests green; define public publish/pull format. |
| P3 Verifiers | HTTP health, coverage, mutation, LLM rubric adapters. | Partly done | Add template contracts and tests for the remaining adapters. |
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
