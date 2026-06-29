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
from dataclasses import dataclass
from typing import List, Optional, Tuple


def _git(args: List[str], cwd: str, check: bool = True) -> Tuple[int, str]:
    proc = subprocess.run(["git"] + args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    if check and proc.returncode != 0:
        raise IntegrationError("git {} failed: {}".format(" ".join(args), proc.stdout))
    return proc.returncode, (proc.stdout or "").strip()


class IntegrationError(RuntimeError):
    pass


class Integration:
    def __init__(self, workspace: str):
        self.workspace = os.path.realpath(workspace)
        os.makedirs(self.workspace, exist_ok=True)
        self.is_git = self._ensure_git()
        self._wt_root = os.path.join(self.workspace, ".loophole_worktrees")

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
