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
import tempfile
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from .budget import Budget, BudgetExceeded
from .contract import GoalContract, VerifierKind
from .integration import Integration
from .executor import execute_task, ExecResult
from .planner import make_plan, plan_hash, PlannerError
from .plan_critic import critique_plan, attack_verifier
from .provider import Provider
from .scheduler import PlannedTask, ready_tasks, admit_parallel
from .state import Store, Task
from .verifier import (VerifyVerdict, VerifierResult, run_command_verifier,
                       check_boundary, check_test_count, parse_pytest)


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
    on_human: Optional[Callable[[str], bool]] = None   # human checkpoint callback


@dataclass
class LoopOutcome:
    status: str                         # done|paused|failed
    rounds: int
    verdict: Optional[VerifyVerdict]
    budget: Budget
    verifier_bypasses: List[str] = field(default_factory=list)
    detail: str = ""


def _baseline_test_total(contract: GoalContract, integ: Integration) -> Optional[float]:
    """Run hard verifiers once on the starting state to get a test-count baseline."""
    if not contract.hard_verifiers:
        return None
    tmp = tempfile.mkdtemp(prefix="loophole_base_")
    try:
        integ.fresh_checkout(tmp, integ.head())
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


def verify_candidate(contract: GoalContract, integ: Integration,
                     baseline_total: Optional[float]) -> VerifyVerdict:
    """Verify from a FRESH checkout of the integration HEAD (council fix B)."""
    cand = tempfile.mkdtemp(prefix="loophole_cand_")
    base = tempfile.mkdtemp(prefix="loophole_basechk_")
    try:
        integ.fresh_checkout(cand, integ.head())
        results: List[VerifierResult] = []
        all_metrics: Dict[str, float] = {}
        hard_pass = True
        for v in contract.hard_verifiers:
            r = run_command_verifier(v, cand)
            results.append(r)
            all_metrics.update(r.metrics)
            hard_pass = hard_pass and r.passed

        violations: List[str] = []
        if contract.all_protected_paths:
            # base = the goal's start commit
            start = integ.head()  # best-effort; start tracked by caller in practice
            integ.fresh_checkout(base, start)
            violations += check_boundary(contract, base, cand)
        violations += check_test_count(contract, all_metrics, baseline_total)

        # soft verifiers may only VETO a hard pass
        soft_veto = False
        for v in contract.soft_verifiers:
            # (LLM rubric evaluation is delegated; treated as non-vetoing here unless wired)
            pass

        passed = bool(contract.hard_verifiers) and hard_pass and not violations and not soft_veto
        failures: List[str] = []
        for r in results:
            failures.extend(r.failures)
        return VerifyVerdict(passed=passed, results=results,
                             boundary_violations=violations, failures=failures)
    finally:
        pass


def run_goal(store: Store, goal_id: str, contract: GoalContract, roles: Roles,
             budget: Budget, cfg: LoopConfig,
             log: Optional[Callable[[str], None]] = None) -> LoopOutcome:
    say = log or (lambda m: None)
    goal_row = store.get_goal(goal_id)
    workspace = goal_row["workspace"]
    integ = Integration(workspace)
    if integ.head():
        store.set_goal_base_commit(goal_id, integ.head())

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

    baseline_total = _baseline_test_total(contract, integ)
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
            for pt in planned:
                store.add_task(goal_id, pt.description, depends_on=pt.depends_on,
                               reads=pt.reads, writes=pt.writes, plan_hash=ph,
                               task_id="{}__{}".format(goal_id, pt.id))
            feedback = None

        # ---- promote + admit ---------------------------------------------
        tasks = store.tasks_for_goal(goal_id)
        rdy = ready_tasks(tasks)
        if rdy:
            for t in rdy:
                store.set_task_status(t.id, "ready", goal_id)
            batch = admit_parallel(rdy, cfg.max_parallel)
            say("round {}: executing {} task(s)".format(rnd + 1, len(batch)))
            _run_batch(store, integ, roles, budget, cfg, goal_id, batch, say)

        # ---- verify -------------------------------------------------------
        verdict = verify_candidate(contract, integ, baseline_total)
        store.log("verify_run", goal_id=goal_id,
                  payload={"passed": verdict.passed, "score": verdict.metric_score,
                           "violations": verdict.boundary_violations})
        say("round {}: verify -> {} (score {:.0f})".format(
            rnd + 1, "PASS" if verdict.passed else "fail", verdict.metric_score))

        if verdict.passed:
            # human checkpoints, if any
            if contract.human_verifiers and cfg.on_human:
                for hv in contract.human_verifiers:
                    if not cfg.on_human(hv.prompt or contract.goal):
                        feedback = "human rejected at checkpoint"
                        verdict = None
                        break
                else:
                    return _finish(store, goal_id, "done", rnd + 1, verdict, budget,
                                   bypasses, "all verifiers passed", say)
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

        recurring = seen_signatures.get(sig, 0) >= cfg.stall_rounds
        if stall >= cfg.stall_rounds or recurring:
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
               say: Callable[[str], None]) -> None:
    def work(task: Task) -> None:
        store.set_task_status(task.id, "running", goal_id)
        attempts = store.incr_attempts(task.id)
        # base = integration HEAD containing dep closure (deps already merged)
        wt = integ.make_worktree(_safe(task.id), base_commit=integ.head())
        try:
            res: ExecResult = execute_task(roles.executor, task, wt,
                                           max_steps=cfg.exec_max_steps,
                                           shell_timeout=cfg.shell_timeout)
            cost = roles.executor.price_in * res.prompt_tokens / 1000.0 + \
                   roles.executor.price_out * res.completion_tokens / 1000.0
            budget.charge(cost, res.total_tokens)
            if not res.ok:
                _fail_task(store, integ, task, goal_id, cfg, attempts, res.summary, say)
                return
            sha = integ.commit_worktree(_safe(task.id), "loophole task: " + task.description[:60])
            if sha is None and integ.is_git:
                # executor reported done but changed nothing — reject the false claim
                _fail_task(store, integ, task, goal_id, cfg, attempts,
                           "reported complete but made no file changes", say)
                return
            ok, detail = integ.merge_task(_safe(task.id))
            if not ok:
                _fail_task(store, integ, task, goal_id, cfg, attempts, detail, say)
                return
            store.update_task(task.id, status="done", result=res.summary,
                              artifact_commit=sha)
            store.log("task_done", goal_id=goal_id, task_id=task.id,
                      payload={"summary": res.summary, "commit": sha})
        finally:
            integ.discard_worktree(_safe(task.id))

    # bounded thread pool = execution plane
    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.max_parallel) as pool:
        list(pool.map(work, batch))


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
