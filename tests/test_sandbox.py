"""S1 — sandbox boundary tests.

These don't just assert on the argv we build; where a real sandbox mechanism is
present they EXECUTE escape attempts and assert the OS actually blocked them.
The execution tests skip cleanly on hosts with no sandbox (e.g. Linux CI without
bubblewrap), so the suite stays green everywhere while still proving the boundary
on developer machines and macOS CI.
"""

import os
import subprocess
import tempfile

import pytest

from loophole import sandbox
from loophole.sandbox import SandboxPolicy, SandboxUnavailable


def _run(command, root, policy):
    argv = sandbox.wrap(command, root, policy)
    return subprocess.run(argv, cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=30)


# ---- fail-closed contract (no execution needed) -------------------------

def test_fail_closed_when_no_mechanism(monkeypatch):
    monkeypatch.setattr(sandbox, "mechanism", lambda: "")
    with pytest.raises(SandboxUnavailable):
        sandbox.wrap("echo hi", "/tmp", SandboxPolicy())


def test_unsafe_optout_runs_unconfined(monkeypatch):
    monkeypatch.setattr(sandbox, "mechanism", lambda: "")
    argv = sandbox.wrap("echo hi", "/tmp", SandboxPolicy(allow_unsandboxed=True))
    assert argv == ["/bin/sh", "-c", "echo hi"]


def test_wrap_is_shell_false_argv(monkeypatch):
    # Whatever the mechanism, wrap() returns a list ending in the sh -c form so
    # callers use shell=False (no double-shell exposure). The final arg is the
    # command, prefixed with the rlimit backstop.
    monkeypatch.setattr(sandbox, "mechanism", lambda: "seatbelt")
    argv = sandbox.wrap("echo hi", "/tmp", SandboxPolicy())
    assert argv[0] == "sandbox-exec"
    assert argv[-3:-1] == ["/bin/sh", "-c"]
    assert argv[-1].endswith("echo hi")
    assert "ulimit" in argv[-1]   # resource-limit backstop applied


# ---- real boundary enforcement (skipped when no mechanism) --------------

requires_sandbox = pytest.mark.skipif(
    not sandbox.available(), reason="no OS sandbox on this host")


@requires_sandbox
def test_write_inside_root_is_allowed():
    with tempfile.TemporaryDirectory() as root:
        r = _run("echo ok > inside.txt && cat inside.txt", root, SandboxPolicy())
        assert r.returncode == 0, r.stdout
        assert os.path.exists(os.path.join(root, "inside.txt"))


@requires_sandbox
def test_write_outside_root_is_blocked():
    with tempfile.TemporaryDirectory() as root:
        # A sibling dir the command must NOT be able to write to.
        outside = tempfile.mkdtemp()
        target = os.path.join(outside, "escaped.txt")
        try:
            r = _run("echo pwned > {}".format(target), root, SandboxPolicy())
            assert r.returncode != 0, "write outside root unexpectedly succeeded"
            assert not os.path.exists(target), "sandbox failed to block external write"
        finally:
            if os.path.exists(target):
                os.remove(target)
            os.rmdir(outside)


@requires_sandbox
def test_network_denied_by_default():
    with tempfile.TemporaryDirectory() as root:
        # Pure-Python TCP connect so we don't depend on curl/nc being installed.
        probe = (
            "python3 -c \"import socket,sys;"
            "s=socket.socket();s.settimeout(5);"
            "sys.exit(0) if s.connect_ex(('1.1.1.1',443))==0 else sys.exit(7)\""
        )
        r = _run(probe, root, SandboxPolicy(allow_network=False))
        assert r.returncode != 0, "network reached despite deny policy"


def test_python_shim_lets_agents_run_bare_python():
    """Out-of-the-box UX: modern macOS ships only python3, but agents reflexively
    run `python`. The Toolbelt shim must make `python` resolve inside the sandbox."""
    from loophole import sandbox as sb
    from loophole.tools import Toolbelt, _python_shim_dir
    if sb.mechanism() not in ("seatbelt", "bwrap"):
        pytest.skip("no OS sandbox on this host")
    if _python_shim_dir() is None and __import__("shutil").which("python") is None:
        pytest.skip("no python3 to shim to")
    root = tempfile.mkdtemp(prefix="loophole_shim_test_")
    tb = Toolbelt(root, timeout=30)
    r = tb.run_shell('python -c "print(42)"')
    assert r.ok, r.output
    assert "42" in r.output
    assert "command not found" not in r.output


def test_trusted_executor_bypasses_sandbox_for_credentials():
    """A trusted framework adapter runs OUTSIDE the OS sandbox so it can reach its
    own credentials (e.g. the keychain for a subscription login). Proof: a trusted
    Toolbelt can read a host file OUTSIDE the worktree that a sandboxed one cannot."""
    from loophole import sandbox as sb
    from loophole.tools import Toolbelt
    if sb.mechanism() not in ("seatbelt", "bwrap"):
        pytest.skip("no OS sandbox on this host")
    root = tempfile.mkdtemp(prefix="loophole_trust_")
    secret = os.path.join(tempfile.mkdtemp(prefix="loophole_host_"), "cred")
    with open(secret, "w") as f:
        f.write("TOKEN123")
    # sandboxed (confine_reads): cannot read the host file outside the worktree
    sandboxed = Toolbelt(root, timeout=20, confine_reads=True)
    r1 = sandboxed.run_shell("cat {}".format(secret))
    assert "TOKEN123" not in r1.output
    # trusted: bypasses the sandbox, can read it (as the framework's own creds)
    trusted = Toolbelt(root, timeout=20, trusted=True)
    r2 = trusted.run_shell("cat {}".format(secret))
    assert r2.ok and "TOKEN123" in r2.output
