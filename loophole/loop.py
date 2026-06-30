"""The control loop — orchestration spine.

Per round:
  1. promote pending->ready when deps are done
  2. admit a disjoint-write-set batch and execute each in its own worktree
  3. merge successful tasks serially (merge-train); rebase happens via fresh HEAD
  4. verify the merged candidate from a FRESH checkout (verifier boundary)
  5. progress is measured by verifier metric delta, NOT task count
  6. stuckness score (verifier-distance + failure-signature recurrence) triggers replan
  7. circuit breakers: per-task attempts, degenerate-plan (plan_hash), max rounds, budget

Concurrency: a bounded ThreadPool is the execution plane; the loop is the control
plane. Worktree/subprocess work is blocking and disk-heavy, so threads with a hard
cap (council critique E) are the right model here, not unbounded async fanout.
"""

from __future__ import annotations

import concurrent.futures
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

from .budget import Budget, BudgetExceeded
from .contract import GoalContract, VerifierKind
from .integration import Integration
from .executor import ExecResult, ReActExecutor, CommandExecutor
from .planner import make_plan, plan_hash, PlannerError
from .plan_critic import critique_plan, attack_verifier
from .provider import Provider
from .scheduler import PlannedTask, ready_tasks, admit_parallel
from .state import Store, Task
from .verifier import (VerifyVerdict, VerifierResult, run_command_verifier,
                       check_boundary, check_test_count, parse_pytest,
                       evaluate_soft_verifier)


@dataclass
class Roles:
    planner: Provider
    executor: Provider
    critic: Provider
    cheap: Optional[Provider] = None      # auto-downgrade target

    def downgrade(self) -> None:
        if self.cheap:
            self.planner = self.cheap
            self.executor = self.cheap
            self.critic = self.cheap


@dataclass
class LoopConfig:
    max_parallel: int = 4
    max_attempts_per_task: int = 3
    stall_rounds: int = 3
    degenerate_repeats: int = 3
    exec_max_steps: int = 12
    shell_timeout: int = 120
    skip_plan_critique: bool = False
    allow_no_git: bool = False     # opt-in to shared-workspace mode when not a git repo
    on_human: Optional[Callable[[str], bool]] = None   # human checkpoint callback
    executor_command: Optional[str] = None  # VIS-1: bring-your-own external agent
                                            # (e.g. 'claude -p {task}'); None = ReAct


@dataclass
class LoopOutcome:
    status: str                         # done|paused|failed
    rounds: int
    verdict: Optional[VerifyVerdict]
    budget: Budget
    verifier_bypasses: List[str] = field(default_factory=list)
    detail: str = ""


def _baseline_test_total(contract: GoalContract, integ: Integration,
                         base_commit: Optional[str] = None) -> Optional[float]:
    """Run hard verifiers once on the ORIGINAL start commit for a test-count baseline.

    C5 fix: checkout the stored ``base_commit`` (not current HEAD) so the baseline
    stays stable across resume/partial-merge instead of drifting.
    """
    if not contract.hard_verifiers:
        return None
    tmp = tempfile.mkdtemp(prefix="loophole_base_")
    try:
        integ.fresh_checkout(tmp, base_commit or integ.head())
        total = 0.0
        seen = False
        for v in contract.hard_verifiers:
            r = run_command_verifier(v, tmp)
            if "pytest" in (v.command or ""):
                total += r.metrics.get("total", 0)
                seen = True
        return total if seen else None
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _reset_orphan_running(store: Store, goal_id: str) -> int:
    """CARCH-1: reset crash-orphaned 'running' tasks (no live worker) back to
    'pending' so the loop re-runs them instead of wedging. Returns the count."""
    orphans = [t for t in store.tasks_for_goal(goal_id) if t.status == "running"]
    for t in orphans:
        store.set_task_status(t.id, "pending", goal_id)
    return len(orphans)


def verify_candidate(contract: GoalContract, integ: Integration,
                     baseline_total: Optional[float],
                     base_commit: Optional[str] = None,
                     soft_judge: Optional[Provider] = None,
                     gate_only: bool = False) -> VerifyVerdict:
    """Verify from a FRESH checkout of the integration HEAD (council fix B).

    base_commit: the goal's ORIGINAL start commit. The protected-path boundary
    compares the candidate against THIS, not against current HEAD — otherwise
    tampering already merged into HEAD would be invisible (GAP 1 fix).
    soft_judge: provider used to evaluate soft (rubric) verifiers, which may only
    VETO a hard pass (GAP 2 fix).
    gate_only: when True, run ONLY the deterministic checks (hard verifiers,
    protected-path boundary, test-count floor) and skip the LLM soft judge. Used
    by the per-merge gate (R3) so every merge is cheaply re-verified before it is
    published to HEAD; the soft judge and human checkpoint stay at the round level.
    """
    cand = tempfile.mkdtemp(prefix="loophole_cand_")
    base = tempfile.mkdtemp(prefix="loophole_basechk_")
    try:
        integ.fresh_checkout(cand, integ.head())
        # ARCH-2: the deterministic checks (hard verifiers + boundary + test-count)
        # depend only on the candidate TREE (base_commit/baseline are fixed per
        # goal), so memoise them by tree sha. This eliminates the guaranteed
        # duplicate where a merge gate verifies tree T and the round-level verify
        # re-verifies the same T, and skips re-running a suite on an unchanged tree.
        tree = integ.tree_sha()
        cache = getattr(integ, "_hard_cache", None)
        if cache is None:
            cache = {}
            try:
                integ._hard_cache = cache
            except Exception:
                cache = None
        key = (tree, base_commit, baseline_total)
        cached = cache.get(key) if (cache is not None and tree) else None
        if cached is not None:
            hard_results, all_metrics, hard_pass, violations = cached
        else:
            hard_results = []
            all_metrics = {}
            hard_pass = True
            for v in contract.hard_verifiers:
                r = run_command_verifier(v, cand)
                hard_results.append(r)
                # C4/C6 fix: SUM numeric metrics across verifiers instead of
                # overwriting, so the candidate total matches the summed baseline.
                for k, val in r.metrics.items():
                    all_metrics[k] = all_metrics.get(k, 0.0) + val
                hard_pass = hard_pass and r.passed

            violations = []
            if contract.all_protected_paths:
                # GAP 1 fix: compare against the goal's ORIGINAL start commit.
                start = base_commit or integ.head()
                integ.fresh_checkout(base, start)
                violations += check_boundary(contract, base, cand)
            violations += check_test_count(contract, all_metrics, baseline_total)
            if cache is not None and tree:
                cache[key] = (hard_results, dict(all_metrics), hard_pass, list(violations))

        # Fresh list so soft-verifier appends never mutate the cached hard results.
        results: List[VerifierResult] = list(hard_results)
        violations = list(violations)

        # N1 fix: a goal with NO hard verifier is automatically "ok" so the loop
        # can reach its human checkpoint; otherwise every hard verifier must pass.
        hard_ok = (not contract.hard_verifiers) or hard_pass

        # GAP 2 fix: soft verifiers may only VETO when hard checks are satisfied.
        # Fail-closed-to-human: a soft verifier that abstained (couldn't be
        # evaluated) doesn't veto, but forces a human checkpoint before "done".
        soft_veto = False
        needs_human: List[str] = []
        if not gate_only and contract.soft_verifiers and hard_ok and not violations:
            for v in contract.soft_verifiers:
                sr = evaluate_soft_verifier(v, cand, soft_judge)
                results.append(sr)
                if sr.abstained:
                    needs_human.append("{}: {}".format(
                        sr.name, (sr.output or "indeterminate")[:160]))
                elif not sr.passed:
                    soft_veto = True

        passed = hard_ok and not violations and not soft_veto
        failures: List[str] = []
        for r in results:
            failures.extend(r.failures)
        return VerifyVerdict(passed=passed, results=results,
                             boundary_violations=violations, failures=failures,
                             needs_human=needs_human)
    finally:
        shutil.rmtree(cand, ignore_errors=True)
        shutil.rmtree(base, ignore_errors=True)


def run_goal(store: Store, goal_id: str, contract: GoalContract, roles: Roles,
             budget: Budget, cfg: LoopConfig,
             log: Optional[Callable[[str], None]] = None) -> LoopOutcome:
    say = log or (lambda m: None)
    # Work on a private copy so loop-local tweaks (e.g. forcing max_parallel=1 in
    # shared-workspace mode) never mutate the caller's LoopConfig.
    cfg = replace(cfg)
    goal_row = store.get_goal(goal_id)
    if goal_row is None:  # C7 fix: controlled failure on missing/deleted goal
        raise ValueError("no such goal: {}".format(goal_id))
    workspace = goal_row["workspace"]
    integ = Integration(workspace, allow_no_git=cfg.allow_no_git)
    # N5 fix: a missing git repo silently collapses all isolation — refuse it
    # unless the operator explicitly opted in (then force serial execution).
    if not integ.is_git:
        if not cfg.allow_no_git:
            return _finish(store, goal_id, "failed", 0, None, budget, [],
                           "workspace is not a git repo and isolation is required "
                           "(pass allow_no_git to run in shared-workspace mode)", say)
        say("WARNING: no git — running in shared workspace with NO task isolation; "
            "forcing max_parallel=1")
        cfg.max_parallel = 1
    # Capture the ORIGINAL start commit once; resume reuses the stored value so the
    # boundary baseline never drifts to a tampered HEAD (GAP 1).
    base_commit: Optional[str] = goal_row["base_commit"]
    if base_commit is None and integ.head():
        base_commit = integ.head()
        store.set_goal_base_commit(goal_id, base_commit)
    # Seed the verified-green marker (R3 durability) so each gated merge can advance
    # it; the starting commit is the floor.
    if integ.last_green() is None:
        integ.mark_green(base_commit)
    # ARCH-1: recover from a crash that left an ungated merge above the last green
    # commit — roll HEAD back so the run never resumes on a poisoned HEAD. Safe by
    # construction: only loophole-authored commits above green are discarded.
    rolled = integ.reconcile_head()
    if rolled:
        say("reconciled: rolled HEAD back to last verified-green {} "
            "(discarded an ungated merge from a prior crash)".format(rolled[:8]))
        store.log("merge_gate_reconcile", goal_id=goal_id, payload={"reset_to": rolled})
    # CARCH-1: a crash can leave tasks 'running' with no worker. reconcile_head only
    # repairs git; reset orphaned 'running' tasks to 'pending' so the loop re-runs
    # them instead of wedging (need_plan/ready_tasks ignore 'running').
    _n_orphans = _reset_orphan_running(store, goal_id)
    if _n_orphans:
        say("reconciled {} orphaned 'running' task(s) -> pending".format(_n_orphans))
        store.log("task_orphans_reconciled", goal_id=goal_id, payload={"count": _n_orphans})

    # Pre-flight: verifier adversary review (fix F)
    bypasses: List[str] = []
    if not cfg.skip_plan_critique:
        try:
            bypasses = attack_verifier(roles.critic, contract)
            if bypasses:
                say("verifier adversary flagged {} potential bypass(es)".format(len(bypasses)))
                store.log("verifier_bypasses", goal_id=goal_id, payload=bypasses)
        except Exception as e:
            say("verifier adversary skipped: {}".format(e))

    baseline_total = _baseline_test_total(contract, integ, base_commit)
    if baseline_total is not None:
        say("baseline test total: {}".format(int(baseline_total)))

    prev_score: Optional[float] = None
    stall = 0
    seen_plan_hashes: Dict[str, int] = {}
    seen_signatures: Dict[str, int] = {}
    feedback: Optional[str] = None
    verdict: Optional[VerifyVerdict] = None
    start_time = time.time()

    def need_plan() -> bool:
        return len([t for t in store.tasks_for_goal(goal_id)
                    if t.status in ("pending", "ready", "running")]) == 0

    for rnd in range(contract.max_rounds):
        if time.time() - start_time > contract.timeout_seconds:
            return _finish(store, goal_id, "paused", rnd, verdict, budget, bypasses,
                           "timeout", say)
        try:
            budget.check()
        except BudgetExceeded as e:
            return _finish(store, goal_id, "paused", rnd, verdict, budget, bypasses,
                           str(e), say)
        if budget.should_downgrade and roles.cheap:
            say("budget at {:.0%} — downgrading roles to cheaper model".format(budget.fraction))
            roles.downgrade()

        # ---- plan / replan ------------------------------------------------
        if need_plan():
            say("round {}: planning".format(rnd + 1))
            try:
                planned = make_plan(roles.planner, contract, feedback=feedback)
            except PlannerError as e:
                return _finish(store, goal_id, "failed", rnd, verdict, budget, bypasses,
                               str(e), say)
            ph = plan_hash(planned)
            seen_plan_hashes[ph] = seen_plan_hashes.get(ph, 0) + 1
            if seen_plan_hashes[ph] >= cfg.degenerate_repeats:
                return _finish(store, goal_id, "paused", rnd, verdict, budget, bypasses,
                               "degenerate plan repeated {}x — escalating to human"
                               .format(seen_plan_hashes[ph]), say)
            if not cfg.skip_plan_critique:
                crit = critique_plan(roles.critic, contract, planned)
                if not crit.approved:
                    feedback = "plan critic rejected: " + "; ".join(crit.issues)
                    say("plan critic rejected plan: {}".format(crit.issues))
                    continue
            # N2 fix: namespace task ids per plan round so a replan that reuses
            # the planner's ids (t1, t2, …) can never collide with an existing
            # PRIMARY KEY and crash the run. Remap depends_on into the round too.
            def _rid(pid: str) -> str:
                return "{}__r{}__{}".format(goal_id, rnd, pid)
            for pt in planned:
                try:
                    store.add_task(
                        goal_id, pt.description,
                        depends_on=[_rid(d) for d in pt.depends_on],
                        reads=pt.reads, writes=pt.writes, plan_hash=ph,
                        task_id=_rid(pt.id))
                except Exception as e:  # never let a storage hiccup crash the run
                    say("skip duplicate/invalid task {}: {}".format(pt.id, e))
            feedback = None

        # ---- promote + admit ---------------------------------------------
        tasks = store.tasks_for_goal(goal_id)
        rdy = ready_tasks(tasks)
        if rdy:
            # ARCH-2: only the ADMITTED batch becomes 'ready'. Promoting all of rdy
            # would leave non-admitted tasks stuck 'ready' — invisible to ready_tasks
            # (which only promotes 'pending') yet counted active by need_plan, so they
            # were silently orphaned. Leaving them 'pending' keeps them schedulable.
            batch = admit_parallel(rdy, cfg.max_parallel)
            for t in batch:
                store.set_task_status(t.id, "ready", goal_id)
            say("round {}: executing {} task(s)".format(rnd + 1, len(batch)))
            _run_batch(store, integ, roles, budget, cfg, goal_id, batch, contract,
                       baseline_total, base_commit, say)
            # N4: unblock the DAG so a permanently-failed task can't wedge the goal
            _abandon_unrunnable(store, goal_id, say)

        # ---- verify -------------------------------------------------------
        verdict = verify_candidate(contract, integ, baseline_total,
                                   base_commit=base_commit,
                                   soft_judge=roles.critic)
        store.log("verify_run", goal_id=goal_id,
                  payload={"passed": verdict.passed, "score": verdict.metric_score,
                           "violations": verdict.boundary_violations})
        say("round {}: verify -> {} (score {:.0f})".format(
            rnd + 1, "PASS" if verdict.passed else "fail", verdict.metric_score))

        if verdict.passed:
            work_remaining = any(
                t.status in ("pending", "ready", "running")
                for t in store.tasks_for_goal(goal_id))
            # N1 fix: for a purely human-gated goal (no hard verifier), passed=True
            # only means "nothing failed" — finish the planned work before asking
            # the human, so we don't prompt against an empty workspace.
            if not contract.hard_verifiers and work_remaining:
                continue  # keep executing remaining tasks
            # Soft-judge fail-closed-to-human: a soft verifier that could not be
            # evaluated must not silently complete. Escalate to the human; if
            # running headless (no callback), PAUSE rather than declare done.
            if verdict.needs_human:
                reason = "soft verifier indeterminate: " + "; ".join(verdict.needs_human[:3])
                if cfg.on_human is None:
                    say("soft judge indeterminate, no human available — pausing (fail-closed)")
                    store.log("soft_fail_closed", goal_id=goal_id, payload={"reason": reason})
                    return _finish(store, goal_id, "paused", rnd + 1, verdict, budget,
                                   bypasses, reason, say)
                if not cfg.on_human(contract.goal + "\n\n" + reason):
                    feedback = "human rejected indeterminate soft check"
                    verdict = None
                    for t in store.tasks_for_goal(goal_id):
                        if t.status in ("pending", "ready"):
                            store.set_task_status(t.id, "abandoned", goal_id)
                    continue
                # approved → fall through to normal completion
            if contract.human_verifiers and cfg.on_human:
                approved = all(cfg.on_human(hv.prompt or contract.goal)
                               for hv in contract.human_verifiers)
                if approved:
                    return _finish(store, goal_id, "done", rnd + 1, verdict, budget,
                                   bypasses, "all verifiers passed", say)
                feedback = "human rejected at checkpoint"
                verdict = None
                # abandon completed work so a fresh plan is generated next round
                for t in store.tasks_for_goal(goal_id):
                    if t.status in ("pending", "ready"):
                        store.set_task_status(t.id, "abandoned", goal_id)
                continue
            return _finish(store, goal_id, "done", rnd + 1, verdict, budget, bypasses,
                           "all verifiers passed", say)

        # ---- progress / stuckness ----------------------------------------
        score = verdict.metric_score
        sig = verdict.signature()
        seen_signatures[sig] = seen_signatures.get(sig, 0) + 1
        progressed = prev_score is None or score > prev_score
        if not progressed:
            stall += 1
        else:
            stall = 0
        prev_score = score

        # Stall-based replanning needs an OBJECTIVE progress signal, so it only
        # applies when there are hard verifiers (human-only goals drain their
        # task list and then ask the human; they rely on max_rounds instead).
        recurring = seen_signatures.get(sig, 0) >= cfg.stall_rounds
        if contract.hard_verifiers and (stall >= cfg.stall_rounds or recurring):
            say("stuck (stall={}, sig x{}) — replanning".format(stall, seen_signatures.get(sig, 0)))
            feedback = "no verifier progress. Current failures: " + \
                       "; ".join(verdict.failures[:6])
            # abandon outstanding pending tasks so need_plan() triggers a fresh plan
            for t in store.tasks_for_goal(goal_id):
                if t.status in ("pending", "ready"):
                    store.set_task_status(t.id, "abandoned", goal_id)
            stall = 0

    return _finish(store, goal_id, "failed", contract.max_rounds, verdict, budget,
                   bypasses, "max rounds exhausted", say)


def _run_batch(store: Store, integ: Integration, roles: Roles, budget: Budget,
               cfg: LoopConfig, goal_id: str, batch: List[Task],
               contract: GoalContract, baseline_total: Optional[float],
               base_commit: Optional[str], say: Callable[[str], None]) -> None:
    def work(task: Task) -> None:
        try:
            _work_inner(task)
        except Exception as e:  # N3 fix: a worker raise must not abort the batch
            attempts = store.get_task(task.id).attempts if store.get_task(task.id) else 99
            _fail_task(store, integ, task, goal_id, cfg, attempts,
                       "worker error: {}".format(e), say)

    def _work_inner(task: Task) -> None:
        store.set_task_status(task.id, "running", goal_id)
        attempts = store.incr_attempts(task.id)
        wid = _safe(task.id)
        # Cut the worktree from current HEAD under the git lock. Because ready_tasks
        # only admits tasks whose deps are all 'done' (merged), HEAD already contains
        # the task's full dependency closure (GAP 3). git metadata ops are serialized.
        with integ.git_lock:
            wt = integ.make_worktree(wid, base_commit=integ.head())
        try:
            # VIS-1: the executor is a pluggable backend. An external "bring-your-
            # own" agent runs as a black box; the verifier boundary is unchanged.
            if cfg.executor_command:
                executor = CommandExecutor(cfg.executor_command,
                                           shell_timeout=cfg.shell_timeout)
            else:
                executor = ReActExecutor(roles.executor, max_steps=cfg.exec_max_steps,
                                         shell_timeout=cfg.shell_timeout)
            res: ExecResult = executor.run(task, wt)
            cost = roles.executor.price_in * res.prompt_tokens / 1000.0 + \
                   roles.executor.price_out * res.completion_tokens / 1000.0
            budget.charge(cost, res.total_tokens)
            if not res.ok:
                _fail_task(store, integ, task, goal_id, cfg, attempts, res.summary, say)
                return
            with integ.git_lock:
                # S5: stage and vet the change set against the write allowlist
                # BEFORE committing — an out-of-bounds write never reaches the
                # commit or the merge-train. The executor is asked to stay within
                # its writes; this enforces it.
                changed = integ.stage_changes(wid)
                if not changed and integ.is_git:
                    # executor reported done but changed nothing — reject false claim
                    _fail_task(store, integ, task, goal_id, cfg, attempts,
                               "reported complete but made no file changes", say)
                    return
                wv = contract.write_violations(changed, task.writes)
                if wv:
                    store.log("write_glob_violation", goal_id=goal_id, task_id=task.id,
                              payload={"violations": wv})
                    _fail_task(store, integ, task, goal_id, cfg, attempts,
                               "write-allowlist violation (S5): " + "; ".join(wv[:5]), say)
                    return
                sha = integ.commit_worktree(wid, "loophole task: " + task.description[:60])
                if sha is None and integ.is_git:
                    _fail_task(store, integ, task, goal_id, cfg, attempts,
                               "reported complete but made no file changes", say)
                    return
                pre_merge = integ.head()
                ok, detail = integ.merge_task(wid)
                if not ok:
                    _fail_task(store, integ, task, goal_id, cfg, attempts, detail, say)
                    return
                # Per-merge gate (R3): re-verify the MERGED HEAD before it is
                # published. Deterministic checks only (hard verifiers + boundary
                # + test-count). If the merge verified red, roll HEAD back so a bad
                # merge never poisons the worktrees that branch off it. This holds
                # the git lock — the merge-train is serial by design.
                if integ.is_git and (contract.hard_verifiers or contract.all_protected_paths):
                    try:
                        gate = verify_candidate(contract, integ, baseline_total,
                                                base_commit=base_commit, gate_only=True)
                    except Exception as e:
                        # Never leave a merged-but-unverified HEAD on a gate error:
                        # roll back so a failure can't poison downstream worktrees.
                        integ.reset_hard(pre_merge or "HEAD")
                        _fail_task(store, integ, task, goal_id, cfg, attempts,
                                   "merge gate errored, rolled back: {}".format(e), say)
                        return
                    if not gate.passed:
                        integ.reset_hard(pre_merge or "HEAD")
                        store.log("merge_gate_reject", goal_id=goal_id, task_id=task.id,
                                  payload={"failures": gate.failures[:6],
                                           "violations": gate.boundary_violations})
                        _fail_task(store, integ, task, goal_id, cfg, attempts,
                                   "merge gate failed (R3): " +
                                   "; ".join((gate.failures + gate.boundary_violations)[:5]),
                                   say)
                        return
                    integ.mark_green(integ.head())
            store.update_task(task.id, status="done", result=res.summary,
                              artifact_commit=sha)
            store.log("task_done", goal_id=goal_id, task_id=task.id,
                      payload={"summary": res.summary, "commit": sha})
        finally:
            with integ.git_lock:
                integ.discard_worktree(wid)

    # bounded thread pool = execution plane. map() never raises now because work()
    # swallows+routes worker errors through _fail_task (N3).
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_parallel) as pool:
        list(pool.map(work, batch))


def _abandon_unrunnable(store: Store, goal_id: str, say: Callable[[str], None]) -> bool:
    """N4 fix: abandon pending/ready tasks whose deps can never complete.

    A task that depends on a failed/abandoned task would block forever (ready_tasks
    only promotes when deps are 'done'), wedging the goal. Abandoning them lets
    need_plan() trigger a fresh plan that routes around the failure. Returns True
    if anything was abandoned.
    """
    tasks = store.tasks_for_goal(goal_id)
    existing = {t.id for t in tasks}
    dead = {t.id for t in tasks if t.status in ("failed", "abandoned")}
    changed = False
    # iterate to a fixpoint so transitive dependents are caught too. A dependency
    # that is dead OR does not exist at all (e.g. a task that failed to persist)
    # makes the dependent unrunnable.
    progress = True
    while progress:
        progress = False
        for t in tasks:
            if t.status in ("pending", "ready") and any(
                    (d in dead or d not in existing) for d in t.depends_on):
                store.set_task_status(t.id, "abandoned", goal_id)
                dead.add(t.id)
                changed = progress = True
        tasks = store.tasks_for_goal(goal_id)
    if changed:
        say("abandoned tasks blocked by a failed dependency — will replan")
    return changed


def _fail_task(store: Store, integ: Integration, task: Task, goal_id: str,
               cfg: LoopConfig, attempts: int, detail: str,
               say: Callable[[str], None]) -> None:
    if attempts >= cfg.max_attempts_per_task:
        store.update_task(task.id, status="failed", last_error=detail)
        store.log("task_failed", goal_id=goal_id, task_id=task.id, payload={"error": detail})
        say("task {} failed permanently after {} attempts: {}".format(
            task.id, attempts, detail[:120]))
    else:
        store.update_task(task.id, status="pending", last_error=detail)
        say("task {} attempt {} failed, will retry: {}".format(task.id, attempts, detail[:120]))


def _finish(store: Store, goal_id: str, status: str, rounds: int,
            verdict: Optional[VerifyVerdict], budget: Budget,
            bypasses: List[str], detail: str, say: Callable[[str], None]) -> LoopOutcome:
    store.set_goal_status(goal_id, status)
    integ = Integration(store.get_goal(goal_id)["workspace"])
    integ.cleanup_worktrees()
    say("goal {} -> {} ({})".format(goal_id, status, detail))
    return LoopOutcome(status=status, rounds=rounds, verdict=verdict, budget=budget,
                       verifier_bypasses=bypasses, detail=detail)


def _safe(task_id: str) -> str:
    return task_id.replace("/", "_").replace(":", "_")
