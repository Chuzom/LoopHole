# loophole — Security & Architecture Audit (Council)

**Committee:** GPT-5.5 (OpenAI, via Codex) · Claude Opus 4.8 (synthesis + independent findings) · hermes3:8b (weak third seat — Chuzom routing overrode the requested Gemini 2.5 Pro).
**Scope:** `tools/`, `integration.py`, `verifier.py`, `executor.py`, `provider.py`, `loop.py`, `contract.py` (post-gap-fix tree).
**All findings below were ground-checked against the actual source.**

---

## ⚠️ Meta-finding (the honest threat model)

loophole executes **attacker-influenceable code with the host user's full privileges** and relies on *path checks*, not OS isolation, for containment. Therefore:

> **loophole v0.1 is only safe for TRUSTED goals, TRUSTED repos, and TRUSTED models, run on a throwaway/sandboxed machine.** It is NOT safe to point at untrusted tasks or third-party repos on a machine with secrets.

The README sells "verification boundary" and "sandboxed toolbelt." The sandbox is a path-prefix check; the verifier runs candidate code unconfined. This gap between marketing and containment is the single most important thing to correct.

---

## SECURITY VULNERABILITIES (severity-ranked)

| # | Severity | Issue | Location | Fix |
|---|---|---|---|---|
| S1 | **CRIT** | `run_shell` + verifier run arbitrary commands with host uid, full env, network. No container/jail. | `tools/__init__.py:run_shell`, `verifier.py:run_command_verifier` | Run in container/VM/seccomp jail, read-only host mounts, dropped caps, scrubbed env. Path checks ≠ sandbox. |
| S2 | **CRIT** | **Secret exfiltration:** full `os.environ` (incl. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, cloud creds) is passed to the verifier subprocess. Any verifier/dependency (`pip install …`) can read & POST them out. | `verifier.py:96` `env={**os.environ, …}` | Pass a minimal scrubbed env (allowlist). Never forward provider keys to verifier/executor shells. |
| S3 | **HIGH** | **Git-hook RCE:** agent with shell can set `core.hooksPath` / write `.git/hooks`; `commit`/`merge` then execute it. | `integration.py:_git, commit_worktree, merge_task` | Run all git with `-c core.hooksPath=/dev/null`; deny agent writes to `.git`. |
| S4 | **HIGH** | **Symlink TOCTOU:** `_resolve()` validates `realpath`, but validate→open isn't atomic; agent races a symlink to escape the sandbox. | `tools/__init__.py:_resolve/write_file/read_file` | `os.open(..., O_NOFOLLOW, dir_fd=...)` per component; reject symlinked parents. |
| S5 | **HIGH** | **`allowed_writes`/`writes` never enforced** — only mentioned in the prompt. Agent can edit tests, packaging, anything. | `contract.py` (declared), `tools/`, `integration.py:commit_worktree` (no check) | Diff changed paths vs the task's write-globs before commit/merge; reject undeclared writes. |
| S6 | **HIGH** | **Prompt injection → capability:** task text, file contents, tool output, rubric all flow into executor/planner/soft-judge. Malicious repo file can instruct `run_shell` to exfiltrate. Real RCE, not just bad text. | `executor.py`, `verifier.py:_snapshot/evaluate_soft_verifier`, planner calls | Frame all repo/task text as untrusted data; constrain capabilities OUTSIDE the model (S1/S2/S5). |
| S7 | **HIGH** | **Boundary symlink bypass:** `check_boundary` compares via filesystem `_read` (follows symlinks); a protected file swapped for a symlink with matching bytes passes. Type/mode/deletion changes missed. | `verifier.py:check_boundary/_glob_files/_read` | Compare `git ls-tree` object IDs + modes + symlink blobs, not dereferenced file bytes. |
| S8 | **MED** | **Persistent cross-project data leak:** one global `~/.loophole/loophole.db` stores plans, task results, and shell output (may contain secrets) from **every** goal on the machine, forever, unencrypted. | `cli.py:_default_db`, `state.py:events` | Per-workspace DB by default; redact/limit stored output; retention policy. |
| S9 | **MED** | `shell=True` on verifier command — metachar injection if any part is generated/config-derived. | `verifier.py:run_command_verifier` | argv array + `shell=False`, or operator allowlist. |
| S10 | **MED** | `fresh_checkout` ignores `git archive` return code and trusts system `tar` for extraction. | `integration.py:fresh_checkout` | Check rc; use `git checkout-index` or Python `tarfile` with member validation. |
| S11 | **MED→HIGH** | **Soft-judge fails OPEN** on no-judge / crash / bad JSON / injection. A safety check that returns "pass" under attack provides zero safety. | `verifier.py:evaluate_soft_verifier/_parse_json_obj` | Fail to **"needs human review"**, not pass. Schema-validate. Frame candidate files as untrusted. |

---

## CONCEPTUAL / THREAT-MODEL GAPS

1. **"Verification boundary" doesn't make the verifier trusted.** A passing hard verifier only proves *attacker-controlled code exited 0 in a permissive host env*. Real boundary = sandbox + trusted tests mounted separately. (GPT-5.5)
2. **Unlimited budget by default.** `max_cost=0` = no ceiling. A confused/malicious model + remote API = runaway spend; `run_shell` infinite loops are caught per-call but replanning can churn to `max_rounds`. Default to a finite ceiling. (Opus)
3. **Dependency-closure is asserted, not proven.** Worktrees cut from HEAD assume the *planner declared every real dependency and write*. An omitted dep/write silently breaks isolation; git history records what happened, not that the plan was complete. (GPT-5.5)
4. **Progress/stuckness is gameable.** `metric_score` rewards passing-test count; agent adds trivial passing tests or perturbs failure signatures to dodge stall detection forever. Test-count floor catches only deletions/skips, not gutted assertions or neutered fixtures. (GPT-5.5)
5. **`attack_verifier` is advisory only** — it logs bypasses then proceeds, creating false assurance that bypasses are handled. Should be able to *block* on high-confidence bypass. (GPT-5.5)
6. **Resume is only partially correct.** Discarding unmerged worktrees is clean, but a task whose commit merged while its store row still says `running` yields an inconsistent state; evidence needed for deterministic resume is lost. (GPT-5.5 + Opus)
7. **No multi-tenancy / isolation.** Shared global DB, default shared `./loophole-out`, no auth — concurrent goals can collide. (hermes + Opus)

---

## CORRECTNESS / CONCURRENCY BUGS

1. **`_baseline_test_total` uses `integ.head()`, not the stored `base_commit`** → on resume after partial merges the baseline drifts, weakening `expected_test_delta`. (`loop.py:83`) — same class as the GAP-1 bug, missed in the first fix.
2. **Multi-verifier metric clobber:** `all_metrics.update(r.metrics)` overwrites; `check_test_count` then compares only the last verifier's total to a baseline that *summed* all verifiers. (`loop.py:118`, `verifier.py:check_test_count`)
3. **`Integration` methods don't acquire `git_lock` internally** — only some call sites do. `verify_candidate`/`_baseline_test_total`/`cleanup_worktrees` create separate `Integration` instances and call git unlocked; latent corruption if ever called concurrently. (`integration.py`, `loop.py`)
4. **`_safe()` branch-name collisions:** only replaces `/` and `:`; distinct task ids can map to one branch/worktree, and other ref-illegal chars remain. (`loop.py:_safe`)
5. **Loose `TASK_COMPLETE` match:** substring anywhere in assistant text marks done. Use exact first-line sentinel. (`executor.py`)
6. **Anthropic provider flattens tool results to user text** — breaks native tool protocol, eases instruction/data confusion. (`provider.py:AnthropicProvider`)
7. **`_snapshot()` nondeterministic traversal** (`os.walk` dirs unsorted) → unstable soft-judge inputs; 25-file/20KB cutoff can omit the relevant file. (`verifier.py:_snapshot`)
8. **`fresh_checkout` rc unchecked** (also listed S10) → empty/invalid checkout silently verified.
9. **SQLite concurrency:** single connection + RLock is correct for integrity, but every worker thread serializes on it; under high `max_parallel` it's a contention bottleneck (not corruption). (state.py)
10. **`run_shell` timeout** may still hang on `communicate()` if grandchildren escaped the process group. (`tools/__init__.py`)

---

## Prioritized remediation (do in this order)

1. **S2** scrub env to verifier/executor (cheap, stops secret exfil) — *highest value/effort*.
2. **S3** `core.hooksPath=/dev/null` on all git calls (one-line, kills a HIGH RCE).
3. **S5** enforce write-globs before commit (makes the boundary real).
4. **S11** soft-judge fail-*closed* to human (restores its purpose).
5. **Conceptual #2** finite default budget.
6. **S1** the big one: real OS sandbox for `run_shell` + verifier (container). Everything else is mitigation around this missing primitive.
7. Correctness bugs #1–#4 (small, real).

---

## Limitations of this audit

- The intended **Gemini 2.5 Pro** seat was overridden to a local 8B model by Chuzom routing (`prefer_ollama`), so seat diversity is weaker than planned — effectively **GPT-5.5 + Opus 4.8** with a weak third. A future pass should force Gemini/Claude-API seats by disabling Chuzom's agent route for the audit.
- No dynamic testing/fuzzing was performed; findings are from source review + targeted ground-checks.
- No dependency CVE scan (`pip-audit`) was run.
