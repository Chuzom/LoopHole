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
