"""S1 — OS-level sandbox for ``run_shell`` and command verifiers.

``run_shell`` executes raw executor-LLM output and the command verifier executes
candidate code (pytest imports agent-written files). ``cwd=`` is NOT confinement:
without an OS sandbox either sink can write outside the worktree, exfiltrate over
the network, or read host secrets. This module wraps a shell command in the
platform's OS sandbox so a command can only:

  * read system files and interpreters (broad read — see note below),
  * read/write within ``root`` and a system temp dir,
  * reach the network only when the caller explicitly opts in.

Policy (S1 decisions):
  * Network is DENIED by default; callers opt in per-invocation.
  * If no sandbox mechanism is available we FAIL CLOSED — ``SandboxUnavailable``
    is raised rather than silently running unconfined. The only bypass is an
    explicit ``allow_unsandboxed=True``, which callers must log loudly.

Note on reads: v1 confines WRITES and NETWORK, not reads. Locking reads under
Seatbelt is brittle (every dyld/interpreter path must be enumerated) and, with
network denied and provider secrets scrubbed from the environment, a read cannot
leave the box. Read-confinement is a documented hardening follow-up.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass
from typing import Dict, List, Tuple


# RLIMIT backstops applied inside the sandbox shell against resource-exhaustion
# DoS (fork/CPU/disk). Only per-process limits that are always safe to lower:
# CPU seconds (RLIMIT_CPU) and file size (RLIMIT_FSIZE, in 512-byte blocks ~= 2GB).
# Deliberately NOT RLIMIT_NPROC — it counts ALL of the real UID's processes, so a
# low value can break fork() on a busy host; process-count caps are a cgroup
# (Linux) / follow-up concern. The wall-clock timeout + process-group kill in the
# callers remain the primary fork-bomb backstop.
_RLIMIT_PREFIX = "ulimit -t 900 2>/dev/null; ulimit -f 4194304 2>/dev/null; "


class SandboxUnavailable(RuntimeError):
    """No OS sandbox mechanism is available and the caller did not opt out.

    Raised instead of running a command unconfined (fail-closed). The operator
    must install a sandbox (bubblewrap on Linux; ``sandbox-exec`` ships with
    macOS) or pass an explicit unsafe opt-out.
    """


@dataclass(frozen=True)
class SandboxPolicy:
    """What a sandboxed command is allowed to do.

    allow_network:      permit outbound network (default deny).
    extra_writable:     absolute paths to also make writable (e.g. a verifier's
                        package cache when it legitimately needs network+install).
    allow_unsandboxed:  if True and no mechanism exists, run UNCONFINED instead of
                        raising. The escape hatch; never the default.
    """

    allow_network: bool = False
    extra_writable: Tuple[str, ...] = ()
    allow_unsandboxed: bool = False
    confine_reads: bool = False   # EXPERIMENTAL (Seatbelt only): restrict reads to
                                  # system dirs + the worktree (blocks ~/.ssh etc.)
    allowed_hosts: Tuple[str, ...] = ()   # recorded for AUDIT; NOT enforced yet — no OS
                                          # sandbox scopes egress by hostname (Seatbelt:
                                          # "host must be * or localhost"; bwrap needs a
                                          # proxy). True per-host scoping = egress-proxy
                                          # sidecar (roadmap). Today: all-or-nothing net.
    pass_env: Tuple[str, ...] = ()        # secret env vars to KEEP (e.g. a framework's
                                          # ANTHROPIC_API_KEY); all others still scrubbed


_SECRET_RE = re.compile(
    r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API|DSN|PEM|PRIVATE|CERT|PASSPHRASE)",
    re.I)
# Credentials embedded in a connection string (e.g. postgres://user:pass@host) —
# caught by VALUE so we don't have to blanket-drop benign *_URL vars (SEC-4).
_CRED_URL_RE = re.compile(r"://[^@\s/]+:[^@\s/]+@")


def scrub_env(extra: Dict[str, str], keep: Tuple[str, ...] = ()) -> Dict[str, str]:
    """Never hand provider API keys / secrets to a sandboxed subprocess.

    Both sinks run agent-influenced code (``run_shell`` runs raw LLM output; the
    verifier imports candidate files via pytest). Passing the full environment
    would let any command or dependency read ANTHROPIC_API_KEY, cloud creds, etc.
    We drop anything that looks like a secret and keep the rest (PATH, HOME…).

    ``keep`` is an explicit allowlist of secret env vars to pass through anyway —
    used only for a first-class executor adapter that genuinely needs ITS key (e.g.
    a Claude Code agent needs ANTHROPIC_API_KEY). Everything else stays scrubbed.
    """
    safe = {k: val for k, val in os.environ.items()
            if not _SECRET_RE.search(k) and not _CRED_URL_RE.search(val or "")}
    for k in keep:
        if k in os.environ:
            safe[k] = os.environ[k]
    safe.update(extra or {})
    return safe


def mechanism() -> str:
    """Return the sandbox mechanism available on this host, or ``""`` if none.

    One of: ``"seatbelt"`` (macOS), ``"bwrap"`` (Linux bubblewrap), ``""``.
    """
    if sys.platform == "darwin" and shutil.which("sandbox-exec"):
        return "seatbelt"
    if shutil.which("bwrap"):
        return "bwrap"
    return ""


def available() -> bool:
    return bool(mechanism())


_TMPDIR_NAME = ".loophole_tmp"


def _scratch_tmp(root: str) -> str:
    """A writable temp dir scoped INSIDE root (so TMPDIR can't escape it)."""
    return os.path.join(os.path.realpath(root), _TMPDIR_NAME)


def _writable_roots(root: str, policy: SandboxPolicy) -> List[str]:
    """Realpath'd set of directories a command may write to.

    Deliberately does NOT include the shared system temp ($TMPDIR, /tmp): that
    would let a command write into other tasks' scratch space. Tools that need
    temp space get a dedicated dir under ``root`` via TMPDIR (see ``wrap``).
    """
    roots = [os.path.realpath(root)]
    roots.extend(os.path.realpath(p) for p in policy.extra_writable)
    # de-dup while preserving order
    seen: set = set()
    out: List[str] = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _seatbelt_profile(root: str, policy: SandboxPolicy) -> str:
    """Generate a deny-by-default Seatbelt profile.

    Confines writes to ``root`` + temp dirs and denies network unless allowed.
    Reads are broadly permitted (see module docstring).
    """
    lines = [
        "(version 1)",
        "(deny default)",
        "(allow process-fork)",
        "(allow process-exec)",
        "(allow signal (target self))",
        "(allow sysctl-read)",
        "(allow mach-lookup)",          # dyld / system frameworks
    ]
    if policy.confine_reads:
        # EXPERIMENTAL read-confinement (SEC-2 spike): allow reads only of system
        # dirs needed to load interpreters/libs plus the worktree + writable roots.
        # Blocks reading host secrets (~/.ssh, ~/.aws, arbitrary $HOME). Brittle —
        # a command needing files outside this set (e.g. a venv outside the
        # worktree) will fail, hence opt-in and off by default. bwrap is unaffected.
        read_subpaths = ["/usr", "/bin", "/sbin", "/System", "/Library",
                         "/private/var/db", "/etc", "/private/etc", "/dev"]
        read_subpaths += _writable_roots(root, policy)
        lines.append("(allow file-read-metadata)")
        for p in read_subpaths:
            esc = os.path.realpath(p).replace("\\", "\\\\").replace('"', '\\"')
            lines.append('(allow file-read* (subpath "{}"))'.format(esc))
        lines.append('(allow file-read* (literal "/"))')
    else:
        lines.append("(allow file-read*)")   # interpreters, libs, source
    lines.append("(deny file-write*)")
    for w in _writable_roots(root, policy):
        # escape backslashes and double-quotes for the Scheme string literal
        esc = w.replace("\\", "\\\\").replace('"', '\\"')
        lines.append('(allow file-write* (subpath "{}"))'.format(esc))
    # character devices a normal program expects to write
    for dev in ("/dev/null", "/dev/zero", "/dev/dtracehelper", "/dev/tty",
                "/dev/stdout", "/dev/stderr", "/dev/random", "/dev/urandom"):
        lines.append('(allow file-write-data (literal "{}"))'.format(dev))
        lines.append('(allow file-ioctl (literal "{}"))'.format(dev))
    # NOTE: Seatbelt cannot scope egress by hostname ("host must be * or localhost"),
    # and bwrap can't either without a netns/proxy — so `allowed_hosts` is recorded
    # for audit but NOT enforced here; egress is all-or-nothing. True per-host scoping
    # needs the egress-proxy sidecar (roadmap). The win Phase 1 DOES deliver: network
    # for a framework agent WITHOUT dropping filesystem confinement (vs allow_unsandboxed).
    if policy.allow_network:
        lines.append("(allow network*)")
    else:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def _bwrap_argv(command: str, root: str, policy: SandboxPolicy, tmp: str) -> List[str]:
    """Bubblewrap invocation (Linux). Untested on macOS hosts."""
    real_root = os.path.realpath(root)
    argv = [
        "bwrap",
        "--die-with-parent",
        "--unshare-all",                 # unshares net too...
    ]
    if policy.allow_network:
        argv.append("--share-net")       # ...re-share only when allowed
    if policy.confine_reads:
        # SEC-2: bind only system dirs needed to run interpreters, NOT the whole
        # host FS — so the command can't read ~/.ssh, ~/.aws, etc. (CI-verified on
        # Linux; macOS uses Seatbelt). Missing paths are skipped.
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        for p in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            if os.path.exists(p):
                argv += ["--ro-bind", p, p]
    else:
        argv += [
            "--ro-bind", "/", "/",       # whole FS read-only
            "--dev", "/dev",
            "--proc", "/proc",
            "--tmpfs", "/tmp",
        ]
    argv += [
        "--bind", real_root, real_root,  # workspace read-write
        "--setenv", "TMPDIR", tmp,       # scoped scratch inside the workspace
    ]
    for w in _writable_roots(root, policy):
        if w != real_root and os.path.isdir(w):
            argv += ["--bind", w, w]
    argv += ["--chdir", real_root, "/bin/sh", "-c", command]
    return argv


def wrap(command: str, root: str, policy: SandboxPolicy) -> List[str]:
    """Return an argv list (for ``subprocess`` with ``shell=False``) that runs
    ``command`` under the OS sandbox, confined to ``root``.

    Raises ``SandboxUnavailable`` if no mechanism exists and the policy does not
    set ``allow_unsandboxed`` (fail-closed).
    """
    mech = mechanism()
    if mech in ("seatbelt", "bwrap"):
        tmp = _scratch_tmp(root)
        os.makedirs(tmp, exist_ok=True)   # scoped temp; covered by the root allow
        env_prefix = ["/usr/bin/env", "TMPDIR=" + tmp]
        limited = _RLIMIT_PREFIX + command
        if mech == "seatbelt":
            profile = _seatbelt_profile(root, policy)
            return ["sandbox-exec", "-p", profile] + env_prefix + ["/bin/sh", "-c", limited]
        return _bwrap_argv(limited, root, policy, tmp)
    if policy.allow_unsandboxed:
        # Explicit, logged-by-caller escape hatch. NOT confined.
        return ["/bin/sh", "-c", command]
    raise SandboxUnavailable(
        "no OS sandbox available (need sandbox-exec on macOS or bwrap on Linux); "
        "refusing to run a command unconfined. Install a sandbox or pass an "
        "explicit unsafe opt-out."
    )
