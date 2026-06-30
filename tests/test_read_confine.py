"""SEC-2 — opt-in Seatbelt read-confinement spike.

When SandboxPolicy.confine_reads is set, the command may read system dirs + the
worktree but NOT arbitrary host paths (e.g. secrets in a sibling temp dir). This
is experimental and off by default (it breaks commands needing files outside the
worktree), so these tests only assert the capability where Seatbelt is present.
"""
import os
import subprocess
import sys
import tempfile

import pytest

from loophole import sandbox
from loophole.sandbox import SandboxPolicy

seatbelt_only = pytest.mark.skipif(
    sandbox.mechanism() != "seatbelt", reason="read-confinement spike is Seatbelt-only")


def _run(command, root, policy):
    argv = sandbox.wrap(command, root, policy)
    return subprocess.run(argv, cwd=root, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, timeout=30)


@seatbelt_only
def test_confined_reads_block_outside_secret():
    with tempfile.TemporaryDirectory() as root:
        # a secret outside the worktree (and outside the confined read set)
        outside = tempfile.mkdtemp()
        secret = os.path.join(outside, "secret.txt")
        with open(secret, "w") as f:
            f.write("TOP-SECRET")
        try:
            confined = SandboxPolicy(confine_reads=True)
            r = _run("cat {}".format(secret), root, confined)
            assert "TOP-SECRET" not in r.stdout, "confined read leaked an outside secret"
            assert r.returncode != 0
            # without confinement the same read is permitted (broad reads, v1 default)
            r2 = _run("cat {}".format(secret), root, SandboxPolicy(confine_reads=False))
            assert "TOP-SECRET" in r2.stdout
        finally:
            os.remove(secret)
            os.rmdir(outside)


@seatbelt_only
def test_confined_reads_still_allow_root_and_exec():
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, "ok.txt"), "w") as f:
            f.write("inside")
        r = _run("cat ok.txt && echo done", root, SandboxPolicy(confine_reads=True))
        assert r.returncode == 0, r.stdout
        assert "inside" in r.stdout and "done" in r.stdout


def test_confine_reads_defaults_off():
    # default profile keeps broad reads (documented v1 behavior)
    p = SandboxPolicy()
    assert p.confine_reads is False
