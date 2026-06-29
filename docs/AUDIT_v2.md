# loophole — Council Audit v2 (post-gap-fix)

**Committee:** GPT-5.5 (OpenAI, via Codex) · Claude Opus 4.8 (synthesis + ground-checks).
*(Gemini 2.5 Pro seat unavailable — Chuzom routing kept overriding it to a local model.)*
**Target:** current loophole code, after the 3 gap-fixes (boundary base-commit, soft-verifier veto, git-op lock).
**Every NEW finding below was ground-checked against the source.**

---

## 🔴 NEW critical findings the first audit missed

### N1 — Human-only goals can NEVER complete (functional dead-end) · `loop.py:138`
```python
passed = bool(contract.hard_verifiers) and hard_pass and not violations and not soft_veto
```
`passed` **requires** `hard_verifiers`. But the CLI (`cli.py:67`) creates a **human-only** contract whenever you run without `--verify`. Result: `verdict.passed` is always `False`, the run **never reaches the human-checkpoint branch**, and it spins to `max_rounds`/timeout instead of asking you. The contract layer says human can grant completion; the verify layer silently contradicts it. **Any goal launched without `--verify` is broken.**
**Fix:** `passed` should be true when `(hard_verifiers and hard_pass) OR (no hard_verifiers and human approves)`, with soft-veto and boundary still applying.

### N2 — Replan crashes the whole run on duplicate task IDs · `loop.py:224-226`
```python
task_id="{}__{}".format(goal_id, pt.id)
```
Task IDs are deterministic from `goal_id` + planner `pt.id`. When a replan reuses any `pt.id` (planners overwhelmingly emit `t1, t2, …` every time), `store.add_task` hits the PRIMARY KEY → `sqlite3.IntegrityError`, **uncaught** → the run dies instead of pausing. The stuck-detection path that triggers replanning therefore frequently **crashes** exactly when it's supposed to recover.
**Fix:** namespace task IDs per plan round (`goal_id__r{round}__{pt.id}`), or `INSERT OR IGNORE`/catch and version.

### N3 — Unhandled worker exception aborts the batch, leaving inconsistent state · `loop.py:237`
`_run_batch` runs `list(pool.map(work, batch))` and `run_goal` has **no try/except** around it. Any raise inside a worker (provider error, git error, `budget.charge`, store write) re-raises in the control thread, **bypassing `_finish`** — tasks stuck `running`, worktrees not cleaned, goal never marked failed.
**Fix:** wrap worker bodies and the batch in try/except; route failures through `_fail_task`/`_finish`.

### N4 — Failed tasks become permanent DAG blockers · `loop.py:_fail_task` + `need_plan`
A task marked `failed` blocks every dependent forever (`ready_tasks` only promotes when deps are `done`). The stuck-loop abandons only `pending`/`ready`, never `failed`. `need_plan` ignores `failed`. So a single permanently-failed task can wedge the goal with no recovery path.
**Fix:** on terminal task failure, either fail the goal explicitly or have replan route around / supersede failed nodes.

### N5 — Non-git fallback silently collapses all isolation · `integration.py:make_worktree/merge_task`
If `_ensure_git()` returns `False` (any `OSError`/git hiccup), `make_worktree` returns the **shared workspace** and `merge_task` returns success ("already in shared workspace"). Parallel tasks then mutate the same directory with **no isolation, no fresh-candidate verification**, and a transient git failure **silently downgrades** to the weakest, unsafe mode instead of stopping.
**Fix:** treat git-unavailable as fatal for parallel/code goals, or force `max_parallel=1` + explicit warning.

---

## SECURITY VULNERABILITIES (status vs prior audit)

| # | Issue | Status | Note |
|---|---|---|---|
| S1 | No OS sandbox for `run_shell`/verifier | **OPEN (CRIT)** | fresh-checkout only cleans the tree; verifier still executes agent code with host creds/network |
| S2 | Full `os.environ` → verifier (secret exfil) | **OPEN (CRIT)** | `verifier.py` still `env={**os.environ, **v.environment}` |
| S3 | Git-hook RCE | **OPEN (HIGH)** | no `core.hooksPath=/dev/null`, no isolated HOME |
| S5 | `allowed_writes` not enforced | **OPEN (HIGH)** | declared writes used only for *scheduling*, never *authorization*; `git add -A` commits everything |
| S11 | Soft-judge fails open | **OPEN→worse** | now part of the boundary AND prompt-injectable via `_snapshot` of agent files; parse-miss/error ⇒ pass |
| **N5** | git-lock only protects some sites | **NEW (HIGH)** | `verify_candidate`/`_baseline_test_total` call git unlocked; lock is per-instance, not a repo lock — concurrent CLI runs race |
| **N6** | `_safe()` branch/path collisions | **NEW (MED-HIGH)** | only replaces `/` and `:`; `a/b` & `a_b` collide → wrong-branch merge or cross-task worktree discard; `make_worktree` force-deletes existing paths |
| **N7** | `cleanup_worktrees`/`discard` rmtree | **NEW (MED)** | `shutil.rmtree(_wt_root)` without lstat/symlink guard |
| **N8** | Resource exhaustion unbounded | **NEW (MED)** | no caps on file count/size, output volume, tree breadth; `_snapshot`/`check_boundary` walk arbitrary trees; multiplied by parallel×rounds |
| S10 | `fresh_checkout` ignores `git archive` rc | **OPEN (MED)** | confirmed; empty/partial checkout silently verified |

---

## CONCEPTUAL GAPS

1. **"Fresh-checkout verification" ≠ a security boundary.** It guarantees a *clean input tree*, not a *trusted verifier* — the verifier still runs hostile code with ambient authority.
2. **Merge-train never re-verifies per merge.** Tasks are marked `done` before the final batch verify. If task B semantically breaks task A without a merge conflict, A stays `done` and its dependency-closure is falsely assumed satisfied. No per-merge rollback.
3. **Dependency closure is planner-*declared*, not enforced.** Under-declared `writes`/`depends_on` ⇒ interfering tasks run in parallel, merge in arbitrary order, and are accepted on task-local `TASK_COMPLETE`. No dynamic read/write capture.
4. **Soft-veto adds no dependable safety.** The veto-only rule is a real improvement, but because the judge fails open and is injectable, it's **advisory UX, not an acceptance control** — and the report should say so.
5. **"Resume is safe" is overstated.** State and git can diverge: merge succeeds but `update_task`/`log` fails → on resume the DB replans against a HEAD that already contains unrecorded changes; `cleanup` can discard `running` worktrees while their DB status stays `running` and `need_plan` ignores `running`.
6. **Boundary diff is incomplete.** `check_boundary` only compares regular-file *bytes* found by `os.walk`; misses deletions, mode/exec-bit changes, symlink-type flips, gitlinks, case-folding aliases.
7. **Progress/stuckness is gameable.** `metric_score` rewards passing-test *count*; signatures are truncated/normalized so recurring failures hide behind reworded messages.

---

## CORRECTNESS BUGS

| # | Bug | Location |
|---|---|---|
| C1 | **Human-only goal never passes** (= N1) | `loop.py:138` |
| C2 | **Duplicate task-id IntegrityError on replan** (= N2) | `loop.py:224` |
| C3 | **No try/except around `_run_batch`** (= N3) | `loop.py:237` |
| C4 | Multi-verifier metric clobber — `all_metrics.update` overwrites | `loop.py:~118` |
| C5 | `_baseline_test_total` uses `head()` not stored `base_commit` (still) | `loop.py:_baseline_test_total` |
| C6 | baseline sums all verifiers but candidate `total` is last-writer ⇒ false/missed test-count violations | `loop.py` + `verifier.check_test_count` |
| C7 | `goal_row["…"]` dereferenced without None check | `loop.py:154,158` |
| C8 | Budget charged post-hoc, checked only per round ⇒ overspend up to `parallel×steps`; concurrent `charge` race | `loop.py:_run_batch` |
| C9 | `fresh_checkout` candidate commit not captured once → HEAD can move between `head()` and `archive` | `loop.py:verify_candidate` |
| C10 | `TASK_COMPLETE` substring match (not exact first-line) | `executor.py` |
| C11 | Anthropic provider flattens tool_use/tool_result → loses tool protocol | `provider.py:AnthropicProvider` |

---

## Recommended remediation order

1. **N1** (human-only never passes) — your default `run` without `--verify` is broken. One-liner.
2. **N2 + N3** (replan crash + unguarded batch) — the recovery path itself crashes; both small.
3. **N4 / N5** (failed-task wedge, git-lock scope).
4. **S2** (env scrub) then **S3** (`core.hooksPath=/dev/null`) — cheap, high security value.
5. **C4–C6** (metric correctness) — small, makes verification trustworthy.
6. **S1** (real OS sandbox) — the large structural item everything else mitigates around.

## Honest framing (unchanged from v1)
loophole proves *"a candidate satisfies the declared contract under a trusted verifier boundary"* — and that boundary is **not yet a security boundary**. Safe only for trusted goals/repos/models on a disposable machine.
