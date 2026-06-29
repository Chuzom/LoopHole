"""Executor toolbelt.

Tools are sandboxed to a single root directory (the task's worktree). All paths
are resolved and checked to stay within the root — no escaping via ``..`` or
absolute paths. ``run_shell`` runs in a process group with a hard timeout so a
cancelled task cannot orphan a child process (council critique E).
"""

from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class ToolResult:
    ok: bool
    output: str

    def __str__(self) -> str:
        return self.output


class Toolbelt:
    def __init__(self, root: str, timeout: int = 120):
        self.root = os.path.realpath(root)
        self.timeout = timeout

    # ---- path safety -----------------------------------------------------
    def _resolve(self, path: str) -> str:
        full = os.path.realpath(os.path.join(self.root, path))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError("path escapes sandbox root: {}".format(path))
        return full

    # ---- tools -----------------------------------------------------------
    def write_file(self, path: str, content: str) -> ToolResult:
        full = self._resolve(path)
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return ToolResult(True, "wrote {} ({} bytes)".format(path, len(content)))

    def read_file(self, path: str) -> ToolResult:
        full = self._resolve(path)
        if not os.path.exists(full):
            return ToolResult(False, "no such file: {}".format(path))
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return ToolResult(True, f.read())

    def list_dir(self, path: str = ".") -> ToolResult:
        full = self._resolve(path)
        if not os.path.isdir(full):
            return ToolResult(False, "not a directory: {}".format(path))
        entries = []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            entries.append(name + ("/" if os.path.isdir(p) else ""))
        return ToolResult(True, "\n".join(entries) or "(empty)")

    def run_shell(self, command: str) -> ToolResult:
        try:
            proc = subprocess.Popen(
                command, shell=True, cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, text=True)
        except OSError as e:
            return ToolResult(False, "spawn failed: {}".format(e))
        try:
            out, _ = proc.communicate(timeout=self.timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            # kill the whole process group so nothing is orphaned
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            out, _ = proc.communicate()
            return ToolResult(False, "TIMEOUT after {}s\n{}".format(self.timeout, out or ""))
        tag = "exit {}".format(rc)
        return ToolResult(rc == 0, "[{}]\n{}".format(tag, out or ""))

    # ---- dispatch --------------------------------------------------------
    def dispatch(self, name: str, args: Dict[str, Any]) -> ToolResult:
        fn: Optional[Callable[..., ToolResult]] = {
            "write_file": self.write_file,
            "read_file": self.read_file,
            "list_dir": self.list_dir,
            "run_shell": self.run_shell,
        }.get(name)
        if fn is None:
            return ToolResult(False, "unknown tool: {}".format(name))
        try:
            return fn(**args)
        except TypeError as e:
            return ToolResult(False, "bad arguments for {}: {}".format(name, e))
        except ValueError as e:
            return ToolResult(False, str(e))


def tool_schemas() -> List[dict]:
    """OpenAI-style tool schemas advertised to the model."""
    def fn(name: str, desc: str, props: dict, required: List[str]) -> dict:
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}}
    s = {"type": "string"}
    return [
        fn("write_file", "Create or overwrite a file in the workspace.",
           {"path": s, "content": s}, ["path", "content"]),
        fn("read_file", "Read a file from the workspace.", {"path": s}, ["path"]),
        fn("list_dir", "List directory contents.", {"path": s}, []),
        fn("run_shell", "Run a shell command in the workspace root.", {"command": s}, ["command"]),
    ]
