"""Executor toolbelt.

File tools (``write_file``/``read_file``/``list_dir``) are confined to a single
root directory: all paths are resolved and checked to stay within the root — no
escaping via ``..`` or absolute paths.

``run_shell`` runs RAW executor-LLM output, so ``cwd=root`` alone is not
containment — it could otherwise write outside the worktree, reach the network,
or read host secrets. S1: it runs under the OS sandbox (see ``loophole.sandbox``)
confined to the root with network denied, with provider secrets scrubbed from the
environment, in a process group with a hard timeout so a cancelled task cannot
orphan a child process (council critique E). If no sandbox mechanism is available
it FAILS CLOSED unless the operator passes ``allow_unsandboxed``.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..sandbox import SandboxPolicy, SandboxUnavailable, scrub_env, wrap


def _python_shim_dir() -> Optional[str]:
    """Modern macOS (and slim Linux images) ship only ``python3`` — but coding
    agents reflexively run ``python foo.py`` and then loop on 'command not found'.
    If ``python`` is missing but ``python3`` exists, build a tiny shim dir with a
    ``python`` launcher so out-of-the-box runs just work. Returns the dir (to put
    on PATH and mark sandbox-readable) or None when no shim is needed/possible."""
    if shutil.which("python"):
        return None
    py3 = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else shutil.which("python3")
    if not py3:
        return None
    d = tempfile.mkdtemp(prefix="loophole_shim_")
    shim = os.path.join(d, "python")
    with open(shim, "w") as f:
        f.write('#!/bin/sh\nexec "{}" "$@"\n'.format(py3))
    os.chmod(shim, 0o755)
    return d


@dataclass
class ToolResult:
    ok: bool
    output: str

    def __str__(self) -> str:
        return self.output


class Toolbelt:
    def __init__(self, root: str, timeout: int = 120, allow_unsandboxed: bool = False,
                 confine_reads: bool = True, network_hosts: tuple = (),
                 pass_env: tuple = (), trusted: bool = False):
        self.root = os.path.realpath(root)
        self.timeout = timeout
        # Fail-closed by default: if no OS sandbox is available, run_shell refuses
        # rather than running unconfined. The operator may opt out explicitly.
        self.allow_unsandboxed = allow_unsandboxed
        # TRUSTED executor: a known-trusted framework agent (e.g. Claude Code) runs
        # OUTSIDE the OS sandbox so it can reach its own credential store (the macOS
        # keychain for a subscription login) — the OS sandbox otherwise blocks that
        # by design. Correctness is still governed downstream: git-worktree isolation
        # + the write-allowlist + the merge gate + the verifier. Off for arbitrary
        # executors; only opt-in-trusted adapters set it.
        self.trusted = trusted
        # Phase 1: a framework-agent executor may need SCOPED egress to its API +
        # ITS api key. Both empty by default (network denied, all secrets scrubbed).
        self.network_hosts = tuple(network_hosts)
        self.pass_env = tuple(pass_env)
        # Give the agent shell a `python` -> `python3` shim when the host lacks a
        # bare `python` (modern macOS), so agents don't dead-loop on 'command not
        # found'. The dir is made sandbox-readable and prepended to PATH per run.
        self._shim_dir = _python_shim_dir()
        # SEC-2: run_shell is the UNTRUSTED agent shell — confine its reads by
        # default so it can't `cat ~/.ssh/id_rsa` and persist host secrets into the
        # task DB. Overridable for commands that legitimately need broader reads.
        self.confine_reads = confine_reads

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
        # S1: confine the command to the OS sandbox (writes within root, no
        # network) and never hand it provider secrets. shell semantics are
        # preserved INSIDE the jail by the wrapped `/bin/sh -c` invocation.
        extra_writable = (self._shim_dir,) if self._shim_dir else ()
        if self.trusted:
            # A trusted framework agent runs OUTSIDE the OS sandbox so it can reach
            # its own credential store (e.g. the keychain for a subscription login).
            # Still cwd-scoped to the worktree; env still scrubbed of OTHER secrets.
            argv = ["/bin/sh", "-c", command]
        else:
            try:
                argv = wrap(command, self.root,
                            SandboxPolicy(allow_unsandboxed=self.allow_unsandboxed,
                                          confine_reads=self.confine_reads,
                                          allow_network=bool(self.network_hosts),
                                          allowed_hosts=self.network_hosts,
                                          extra_writable=extra_writable,
                                          pass_env=self.pass_env))
            except SandboxUnavailable as e:
                return ToolResult(False, "sandbox unavailable: {}".format(e))
        env = scrub_env({}, keep=self.pass_env)
        if self._shim_dir:                       # put the `python` shim first on PATH
            env["PATH"] = self._shim_dir + os.pathsep + env.get("PATH", "")
        try:
            proc = subprocess.Popen(
                argv, cwd=self.root,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True, text=True,
                env=env)
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
