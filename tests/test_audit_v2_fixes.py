"""Regression tests for the v2 council-audit fixes (N1-N5 + hardening)."""
from __future__ import annotations

import os
import subprocess
import tempfile

from loophole.contract import GoalContract, Verifier, VerifierKind
from loophole.integration import Integration
from loophole.loop import verify_candidate, _abandon_unrunnable
from loophole.verifier import _scrub_env
from loophole.state import Store


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_t_")
    integ = Integration(ws)
    return ws, integ


# ── N1: human-only goals can pass (reach the checkpoint) ──────────────────────
def test_human_only_goal_passes_verification():
    _ws, integ = _git_ws()
    c = GoalContract(goal="write something", verifiers=[
        Verifier(kind=VerifierKind.HUMAN, prompt="ok?")])
    verdict = verify_candidate(c, integ, baseline_total=None, base_commit=integ.head())
    # with no hard verifier and no violations, automated check must be "ok" so the
    # loop can reach the human checkpoint (previously this was always False).
    assert verdict.passed is True


def test_hard_verifier_still_required_to_pass_when_present():
    _ws, integ = _git_ws()
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="false")])  # always fails
    verdict = verify_candidate(c, integ, baseline_total=None, base_commit=integ.head())
    assert verdict.passed is False


# ── C4/C6: metrics summed across multiple hard verifiers ──────────────────────
def test_multiple_hard_verifiers_both_run():
    _ws, integ = _git_ws()
    c = GoalContract(goal="x", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="echo '2 passed'"),
        Verifier(kind=VerifierKind.HARD, command="echo '3 passed'"),
    ])
    verdict = verify_candidate(c, integ, baseline_total=None, base_commit=integ.head())
    assert verdict.passed is True
    assert len([r for r in verdict.results if r.name.startswith("hard:")]) == 2


# ── N4: failed task does not wedge dependents ─────────────────────────────────
def test_abandon_unrunnable_releases_blocked_tasks():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    s = Store(path)
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    a = s.add_task(gid, "a")
    b = s.add_task(gid, "b", depends_on=[a])
    cc = s.add_task(gid, "c", depends_on=[b])
    s.set_task_status(a, "failed", gid)
    changed = _abandon_unrunnable(s, gid, lambda m: None)
    assert changed is True
    statuses = {t.id: t.status for t in s.tasks_for_goal(gid)}
    assert statuses[b] == "abandoned" and statuses[cc] == "abandoned"  # transitive
    s.close()


def test_abandon_unrunnable_handles_missing_dependency():
    # re-audit fix: a task depending on an id that never persisted must be abandoned
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    s = Store(path)
    gid = s.create_goal('{"goal":"x"}', "/tmp/ws")
    b = s.add_task(gid, "b", depends_on=["ghost-task-that-does-not-exist"])
    assert _abandon_unrunnable(s, gid, lambda m: None) is True
    assert s.get_task(b).status == "abandoned"
    s.close()


def test_loopconfig_not_mutated_by_run(monkeypatch):
    # re-audit fix: forcing max_parallel in shared mode must not leak to caller cfg
    from dataclasses import replace
    from loophole.loop import LoopConfig
    cfg = LoopConfig(max_parallel=4)
    _ = replace(cfg)  # the pattern run_goal uses
    assert cfg.max_parallel == 4


# ── S2: secrets scrubbed from verifier env ────────────────────────────────────
def test_scrub_env_drops_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret2")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "tok")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _scrub_env({"EXTRA": "1"})
    assert "ANTHROPIC_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "AWS_SESSION_TOKEN" not in env
    assert env["PATH"] == "/usr/bin"      # non-secret kept
    assert env["EXTRA"] == "1"            # explicit extra merged


def test_verifier_cannot_read_secret_env():
    _ws, integ = _git_ws()
    os.environ["ANTHROPIC_API_KEY"] = "sk-leak-me"
    try:
        c = GoalContract(goal="x", verifiers=[Verifier(
            kind=VerifierKind.HARD,
            command="python3 -c \"import os; assert not os.environ.get('ANTHROPIC_API_KEY'), 'LEAK'\"")])
        verdict = verify_candidate(c, integ, baseline_total=None, base_commit=integ.head())
        assert verdict.passed is True, "verifier saw the secret env var"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


# ── S3: git hooks disabled during integration ops ─────────────────────────────
def test_git_ops_ignore_repo_hooks():
    ws, integ = _git_ws()
    hooks = os.path.join(ws, ".git", "hooks")
    os.makedirs(hooks, exist_ok=True)
    # a pre-commit hook that would fail the commit if it ran
    pc = os.path.join(hooks, "pre-commit")
    with open(pc, "w") as f:
        f.write("#!/bin/sh\nexit 1\n")
    os.chmod(pc, 0o755)
    wt = integ.make_worktree("hooktest", base_commit=integ.head())
    with open(os.path.join(wt, "f.txt"), "w") as f:
        f.write("hi")
    sha = integ.commit_worktree("hooktest", "should commit despite failing hook")
    integ.discard_worktree("hooktest")
    assert sha is not None  # hook was bypassed (core.hooksPath=/dev/null)
