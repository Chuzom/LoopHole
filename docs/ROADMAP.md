# LoopHole — Status, Risk Review & Roadmap

_Committee-style review (security · architecture/correctness · product · reliability lenses), synthesized 2026-06-30. Grounded in the current tree; claims marked (inferred) need a verification pass._

---

## 1. Where we are

LoopHole is a **run-until-verified** autonomous engineering loop. Its thesis: an LLM saying "done" is worthless; only a **falsifiable Goal Contract checked by a trusted verifier boundary** can grant completion.

Pipeline (per `loop.py`):

1. **Contract** (`contract.py`) — goal + verifiers (`hard` command / `soft` LLM rubric / `human` checkpoint), protected paths, mutation policy, budgets. Refuses goals that can't define "done".
2. **Plan** (`planner.py` + `plan_critic.py`) — decompose into a task DAG with deps.
3. **Execute** (`executor.py` + `tools/`) — ReAct agents work in isolated git worktrees (`integration.py`).
4. **Merge-train** (`integration.py`) — successful tasks merge serially into HEAD.
5. **Verify** (`verifier.py`) — fresh checkout of merged HEAD; hard checks + boundary (protected-path unchanged vs original base, test-count floor) + soft rubric. Sole authority on "done".

State in SQLite (`state.py`); multi-provider (`provider.py`); budget ceilings (`budget.py`); scheduler/report present.

**Maturity:** late-v0.1. Two prior council audits (`AUDIT.md`, `AUDIT_v2.md`) drove a wave of fixes. Recently closed:

- **S1 — OS sandbox** for `run_shell` + command verifiers (Seatbelt/bwrap, deny-default, network-denied, fail-closed). ✅ this session
- **S11 — soft-judge fail-closed-to-human** (abstains → human, no longer fail-open). ✅ this session

56 tests passing.

---

## 2. Risk register

Severity = impact × likelihood for the intended use (autonomous, unattended runs that merge code).

| # | Risk | Sev | Status / evidence | Mitigation |
|---|------|-----|-------------------|------------|
| R1 | `run_shell`/verifier could escape worktree, reach network, leak `ANTHROPIC_API_KEY` | ~~Critical~~ | **Closed (S1)**. Residual: reads unconfined; bwrap untested on Linux; no CLI opt-out flag | Read-confinement follow-up; validate bwrap in Linux CI; surface `--unsafe-no-sandbox` |
| R2 | **`allowed_writes` not enforced** — agent can write/merge any path in the worktree | **High** | Confirmed: field stored only (`contract.py:75`); executor merely *asked* to comply | **S5:** enforce write-globs at `commit_worktree` — reject or strip out-of-glob changes before merge |
| R3 | **Merge-before-verify** — bad merge poisons HEAD; downstream worktrees inherit it | **High** | Confirmed: `merge_task` then round-level `verify_candidate` (`loop.py:407`,`289`) | **Per-merge re-verify:** stage→verify→publish (fast-forward only on pass), or verify each merge before the next |
| R4 | Soft-judge **prompt injection** via `_snapshot` of agent-written files into the judge | Med | Open; noted in `AUDIT_v2` S11. Fail-closed reduces blast radius but injection can still force a false PASS | Frame candidate text as untrusted/delimited; schema-validate verdict (partly done); keep soft as veto-only |
| R5 | `plan_critic` **fails open** — parse miss ⇒ `approved=True` (`plan_critic.py:66`) | Med | Confirmed | Apply the S11 pattern: unparseable ⇒ reject/replan, not auto-approve |
| R6 | **No resource limits** on sandboxed commands (CPU/mem/disk) — only wall-clock | Med (inferred) | Seatbelt/timeouts don't cap CPU/disk; fork bomb / disk-fill possible in-worktree | `ulimit` prefix in the `sh -c`; cgroups/`--rlimit` on Linux; disk quota on worktree |
| R7 | **Supply chain** via `allow_network` verifiers (`pip install` arbitrary pkgs into candidate env) | Med (inferred) | Network opt-in exists; no pinning/allowlist | Hash-pinned installs; registry allowlist; offline default already in place |
| R8 | **Merge-train concurrency / state races** under `max_parallel` | Med (inferred) | SQLite lock present; serial merge claimed — needs a focused review | Concurrency test harness; document serialization invariants |
| R9 | **Secrets in orchestrator process** (scrubbed only from subprocesses) | Low | `scrub_env` protects children; parent still holds keys | Acceptable v0.1; revisit for multi-tenant |
| R10 | **Dev-environment trust** — the `chuzom` hook injects fabricated answers / false "tool blocked" notices | Meta | Observed repeatedly this session | Don't trust injected content; see memory `chuzom-routing-unreliable` |

**Top 3 to fix next:** R2 (write-glob enforcement), R3 (per-merge re-verify), R5 (plan-critic fail-closed) — all cheap, all close real holes, all align with the fail-closed philosophy now established.

---

## 3. Product roadmap (outcome-oriented)

| Phase | Theme | Outcome | Headline items |
|-------|-------|---------|----------------|
| **P0 — Harden (v0.1)** | Trustworthy boundary | "Safe to run unattended on one repo" | Write-glob enforcement, per-merge re-verify, CLI surface for sandbox + soft-verifier, resource limits |
| **P1 — Usable (v0.2)** | Trust & ergonomics | "A dev defines a goal and trusts the result" | Residual-risk report polish, resumable runs, human-checkpoint UX, contract templates/examples, clear run summaries |
| **P2 — Integrated (v0.3)** | Fit the workflow | "Runs in a team's pipeline" | GitHub Actions/CI integration, container exec backend, multi-repo, model routing + cost dashboard |
| **P3 — Platform (v1.0)** | Govern & scale | "Production autonomous-engineering platform" | Policy/governance engine, signed change provenance, audit trails, remote workers, verifier marketplace |

---

## 4. Technical roadmap

**Now (P0 — close the boundary):**
- [ ] **S5** enforce `allowed_writes` at `commit_worktree` (reject/strip out-of-glob diffs) — R2
- [ ] **Per-merge re-verification**: stage merge → verify → publish; fast-forward only on pass — R3
- [ ] **plan_critic** fail-closed on unparseable verdict — R5
- [ ] **CLI flags**: `--unsafe-no-sandbox` (plumbing exists end-to-end), wire soft-verifier into CLI (engine supports it; no flag)
- [ ] **Resource limits**: `ulimit` prefix in sandboxed `sh -c`; disk quota on worktree — R6
- [ ] **bwrap validation** on a Linux runner; Seatbelt **read-confinement** spike — R1

**Near (P0→P1 — provableness):**
- [ ] End-to-end loop tests with fake providers (cover the soft fail-closed *gate*, not just the verdict)
- [ ] Fuzz `parse_pytest` / `_parse_json_obj`
- [ ] Soft-judge injection hardening (untrusted framing + schema validation) — R4
- [ ] Structured logging / observability over the SQLite event log; deterministic replay from state
- [ ] Concurrency review + race tests for the merge-train — R8

**Mid (P2 — scale the substrate):**
- [ ] Container execution backend (rootless podman/docker) as a sandbox option alongside Seatbelt/bwrap
- [ ] Dependency pinning/allowlist for `allow_network` verifiers — R7
- [ ] Incremental / cached verification; provider abstraction hardening
- [ ] Multi-repo goal graphs

**Long (P3 — govern):**
- [ ] Policy engine for mutation + egress; per-org guardrails
- [ ] Signed provenance of merged changes; full audit trail
- [ ] Remote/distributed workers; multi-tenant isolation (revisit R9)

---

## 5. One-paragraph synthesis

LoopHole's core bet — *verification is the only authority on done* — is sound and increasingly well-defended: the execution boundary is now OS-sandboxed and fail-closed, and the judge no longer rubber-stamps when it can't actually judge. The remaining high-severity gaps are not in the philosophy but in **enforcement of policy the system already declares**: it promises a write-allowlist it doesn't enforce (R2) and verifies a candidate it has already merged (R3). Close those two, make `plan_critic` fail-closed like everything else (R5), and v0.1 graduates from "impressive prototype with a coherent threat model" to "safe to leave running." Everything past that is ergonomics, integration, and scale.
