"""S1 Stage 2 — prove run_shell and the command verifier are actually confined.

Where a real sandbox exists these EXECUTE the attack (write outside the worktree,
echo a secret) and assert it's blocked. The fail-closed tests need no sandbox and
run everywhere.
"""

import os
import tempfile

import pytest

from loophole import sandbox
from loophole.contract import Verifier, VerifierKind
from loophole.tools import Toolbelt
from loophole.verifier import run_command_verifier

requires_sandbox = pytest.mark.skipif(
    not sandbox.available(), reason="no OS sandbox on this host")


# ---- run_shell -----------------------------------------------------------

@requires_sandbox
def test_run_shell_blocks_write_outside_root():
    with tempfile.TemporaryDirectory() as root:
        outside = tempfile.mkdtemp()
        target = os.path.join(outside, "escaped.txt")
        try:
            belt = Toolbelt(root)
            res = belt.run_shell("echo pwned > {}".format(target))
            assert not res.ok
            assert not os.path.exists(target), "run_shell escaped the worktree"
        finally:
            if os.path.exists(target):
                os.remove(target)
            os.rmdir(outside)


@requires_sandbox
def test_run_shell_scrubs_provider_secret(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    with tempfile.TemporaryDirectory() as root:
        belt = Toolbelt(root)
        res = belt.run_shell("echo KEY=$ANTHROPIC_API_KEY")
        assert "sk-should-not-leak" not in res.output


@requires_sandbox
def test_run_shell_write_inside_root_works():
    with tempfile.TemporaryDirectory() as root:
        belt = Toolbelt(root)
        res = belt.run_shell("echo hi > note.txt && cat note.txt")
        assert res.ok, res.output
        assert os.path.exists(os.path.join(root, "note.txt"))


@requires_sandbox
def test_run_shell_confines_reads_by_default():
    # SEC-2: the untrusted agent shell can't read host files outside the worktree.
    with tempfile.TemporaryDirectory() as root:
        outside = tempfile.mkdtemp()
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "w") as f:
            f.write("HOST-SECRET-MATERIAL")
        try:
            res = Toolbelt(root).run_shell("cat {}".format(secret))
            assert "HOST-SECRET-MATERIAL" not in res.output   # confined by default
            # explicit opt-out restores broad reads
            res2 = Toolbelt(root, confine_reads=False).run_shell("cat {}".format(secret))
            assert "HOST-SECRET-MATERIAL" in res2.output
            # in-worktree work still functions under confinement
            ok = Toolbelt(root).run_shell("echo hi > note.txt && cat note.txt")
            assert ok.ok and "hi" in ok.output
        finally:
            if os.path.exists(secret):
                os.remove(secret)
            os.rmdir(outside)


def test_run_shell_fail_closed_without_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "mechanism", lambda: "")
    with tempfile.TemporaryDirectory() as root:
        belt = Toolbelt(root)  # default: not opted out
        res = belt.run_shell("echo hi")
        assert not res.ok
        assert "sandbox unavailable" in res.output


def test_run_shell_unsafe_optout_runs(monkeypatch):
    monkeypatch.setattr(sandbox, "mechanism", lambda: "")
    with tempfile.TemporaryDirectory() as root:
        belt = Toolbelt(root, allow_unsandboxed=True)
        res = belt.run_shell("echo hi")
        assert res.ok and "hi" in res.output


# ---- command verifier ----------------------------------------------------

def test_verifier_fail_closed_without_sandbox(monkeypatch):
    monkeypatch.setattr(sandbox, "mechanism", lambda: "")
    with tempfile.TemporaryDirectory() as cwd:
        v = Verifier(kind=VerifierKind.HARD, command="true")
        res = run_command_verifier(v, cwd)
        assert not res.passed
        assert "sandbox unavailable" in res.failures


@requires_sandbox
def test_verifier_runs_under_sandbox():
    with tempfile.TemporaryDirectory() as cwd:
        v = Verifier(kind=VerifierKind.HARD, command="echo ok && exit 0")
        res = run_command_verifier(v, cwd)
        assert res.passed, res.output


def test_verifier_allow_network_roundtrips():
    v = Verifier(kind=VerifierKind.HARD, command="true", allow_network=True)
    assert v.to_dict()["allow_network"] is True
    assert Verifier.from_dict(v.to_dict()).allow_network is True
    # default stays closed
    assert Verifier(kind=VerifierKind.HARD, command="true").allow_network is False
