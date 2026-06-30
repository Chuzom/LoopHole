"""Commit-graph integration (council critique A).

Each task runs in its own git worktree created from the CURRENT integration
commit that already contains its dependency closure — never from a stale
round-start base. On success the task's changes are committed and merged back
serially through this single integration point. The git history IS the
dependency graph, and resume is safe because un-merged worktrees are simply
discarded (council critique #7).

If the workspace is not a git repo we degrade gracefully to a single shared
workspace (the "no-sandbox lane" noted in the council dissent) — suitable for
non-code goals.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple


def _git(args: List[str], cwd: str, check: bool = True) -> Tuple[int, str]:
    # S3 fix: disable repo hooks so agent-written .git/hooks or core.hooksPath can
    # never execute during our commit/merge lifecycle (git-hook RCE). Also pin a
    # quiet, non-interactive environment.
    cmd = ["git", "-c", "core.hooksPath=/dev/null"] + args
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, env=env)
    if check and proc.returncode != 0:
        raise IntegrationError("git {} failed: {}".format(" ".join(args), proc.stdout))
    return proc.returncode, (proc.stdout or "").strip()


class IntegrationError(RuntimeError):
    pass


class Integration:
    def __init__(self, workspace: str, allow_no_git: bool = True):
        self.workspace = os.path.realpath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self.is_git = self._ensure_git()
        # N5: the caller (run_goal) decides whether a non-git workspace is allowed;
        # allow_no_git here just records intent for callers that inspect it.
        self.allow_no_git = allow_no_git
        self._wt_root = os.path.join(self.workspace, ".loophole_worktrees")
        # git index/refs are NOT safe under concurrent mutation (council critique E):
        # serialize all repo-metadata operations while executors run in parallel.
        self.git_lock = threading.RLock()

    def _ensure_git(self) -> bool:
        if os.path.isdir(os.path.join(self.workspace, ".git")):
            return True
        try:
            _git(["init", "-q"], self.workspace)
            _git(["config", "user.email", "loophole@local"], self.workspace)
            _git(["config", "user.name", "loophole"], self.workspace)
            # ensure at least one commit exists
            keep = os.path.join(self.workspace, ".gitkeep")
            if not os.listdir(self.workspace) or not self._has_commit():
                open(keep, "a").close()
                _git(["add", "-A"], self.workspace)
                _git(["commit", "-q", "-m", "loophole: initial workspace"], self.workspace)
            return True
        except (IntegrationError, OSError):
            return False

    def _has_commit(self) -> bool:
        rc, _ = _git(["rev-parse", "HEAD"], self.workspace, check=False)
        return rc == 0

    def head(self) -> Optional[str]:
        if not self.is_git:
            return None
        _, out = _git(["rev-parse", "HEAD"], self.workspace)
        return out

    # ---- worktree lifecycle ---------------------------------------------
    def make_worktree(self, task_id: str, base_commit: Optional[str] = None) -> str:
        """Create a worktree for a task off the dependency-closure commit."""
        if not self.is_git:
            return self.workspace  # shared lane
        os.makedirs(self._wt_root, exist_ok=True)
        path = os.path.join(self._wt_root, task_id)
        branch = "loophole/" + task_id
        if os.path.exists(path):
            self.discard_worktree(task_id)
        base = base_commit or self.head()
        _git(["worktree", "add", "-q", "-f", "-b", branch, path, base], self.workspace)
        return path

    def stage_changes(self, task_id: str) -> List[str]:
        """Stage all worktree changes and return the changed repo-relative paths.

        Used to enforce the write allowlist BEFORE committing (S5), so an
        out-of-bounds change never reaches a commit or the merge-train.
        """
        if not self.is_git:
            return []
        path = os.path.join(self._wt_root, task_id)
        _git(["add", "-A"], path)
        _, out = _git(["diff", "--cached", "--name-only"], path)
        return [ln.strip() for ln in out.splitlines() if ln.strip()]

    def commit_worktree(self, task_id: str, message: str) -> Optional[str]:
        """Commit all changes in the task's worktree. Returns the commit sha."""
        if not self.is_git:
            return None
        path = os.path.join(self._wt_root, task_id)
        _git(["add", "-A"], path)
        rc, _ = _git(["diff", "--cached", "--quiet"], path, check=False)
        if rc == 0:
            return None  # nothing changed
        _git(["commit", "-q", "-m", message], path)
        _, sha = _git(["rev-parse", "HEAD"], path)
        return sha

    def merge_task(self, task_id: str) -> Tuple[bool, str]:
        """Merge the task branch into the integration HEAD. Serial merge-train.

        Returns (ok, detail). On conflict, aborts the merge and reports.
        """
        if not self.is_git:
            return True, "no-git lane: changes already in shared workspace"
        branch = "loophole/" + task_id
        rc, out = _git(["merge", "--no-edit", "-q", branch], self.workspace, check=False)
        if rc != 0:
            _git(["merge", "--abort"], self.workspace, check=False)
            return False, "merge conflict: " + out
        return True, "merged"

    def reset_hard(self, commit: str) -> None:
        """Roll the integration HEAD back to ``commit`` (R3 merge-gate rollback).

        Used to undo a merge that verified red, so a bad merge never persists in
        HEAD where downstream worktrees would branch off it.
        """
        if not self.is_git:
            return
        _git(["reset", "--hard", "-q", commit], self.workspace, check=False)

    _GREEN_REF = "refs/loophole/last_green"

    def mark_green(self, commit: Optional[str]) -> None:
        """Record ``commit`` as the latest verified-green integration HEAD (R3).

        Durable across crashes (it's a git ref). A startup reconciliation can use
        it to roll an ungated, crash-left HEAD back to the last green commit.
        """
        if not self.is_git or not commit:
            return
        _git(["update-ref", self._GREEN_REF, commit], self.workspace, check=False)

    def last_green(self) -> Optional[str]:
        """The last commit recorded green via ``mark_green``, or None."""
        if not self.is_git:
            return None
        rc, out = _git(["rev-parse", "--verify", "-q", self._GREEN_REF + "^{commit}"],
                       self.workspace, check=False)
        out = (out or "").strip()
        return out if rc == 0 and out else None

    @staticmethod
    def _is_loophole_commit(subject: str) -> bool:
        s = subject.strip()
        return (s.startswith("loophole task:") or s.startswith("loophole:")
                or s.startswith("Merge branch 'loophole/"))

    def reconcile_head(self) -> Optional[str]:
        """Crash recovery for the per-merge gate (ARCH-1 / R3).

        A hard kill between a merge and its gate rollback can leave HEAD ahead of
        the last verified-green commit. On startup, roll HEAD back to last_green —
        but ONLY when it is safe: last_green must be an ancestor of HEAD and EVERY
        commit in (last_green, HEAD] must be loophole-authored. If any commit is a
        user's (or HEAD has diverged / green is ahead), do nothing — never discard
        work we didn't create. Returns the sha reset to, or None.
        """
        if not self.is_git:
            return None
        g = self.last_green()
        head = self.head()
        if not g or not head or head == g:
            return None
        # green must be a strict ancestor of HEAD
        rc, _ = _git(["merge-base", "--is-ancestor", g, head], self.workspace, check=False)
        if rc != 0:
            return None
        rc, out = _git(["log", "--format=%s", "{}..{}".format(g, head)],
                       self.workspace, check=False)
        if rc != 0:
            return None
        subjects = [ln for ln in (out or "").splitlines() if ln.strip()]
        if not subjects or not all(self._is_loophole_commit(s) for s in subjects):
            return None  # a user commit sits above green — refuse to roll back
        self.reset_hard(g)
        return g

    def discard_worktree(self, task_id: str) -> None:
        if not self.is_git:
            return
        path = os.path.join(self._wt_root, task_id)
        _git(["worktree", "remove", "--force", path], self.workspace, check=False)
        if os.path.exists(path):
            shutil.rmtree(path, ignore_errors=True)
        _git(["branch", "-D", "loophole/" + task_id], self.workspace, check=False)

    def fresh_checkout(self, dest: str, commit: Optional[str] = None) -> str:
        """Produce a clean checkout of a commit for verification (council fix B)."""
        if not self.is_git:
            # copy the workspace minus worktree scratch
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(self.workspace, dest,
                            ignore=shutil.ignore_patterns(".loophole_worktrees", ".git"))
            return dest
        commit = commit or self.head()
        if os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        # archive the tree at `commit` into dest — no .git, truly clean
        proc = subprocess.run(["git", "archive", commit], cwd=self.workspace,
                              stdout=subprocess.PIPE)
        tar = subprocess.run(["tar", "-x", "-C", dest], input=proc.stdout)
        if tar.returncode != 0:
            raise IntegrationError("fresh_checkout extract failed")
        return dest

    def cleanup_worktrees(self) -> None:
        if self.is_git:
            _git(["worktree", "prune"], self.workspace, check=False)
            if os.path.isdir(self._wt_root):
                shutil.rmtree(self._wt_root, ignore_errors=True)
