"""First-run UX — a bare `loophole` welcomes/orients; empty runs/ls give a hint."""
from __future__ import annotations

import os
import tempfile

from click.testing import CliRunner

from loophole.cli import main


def test_bare_loophole_shows_welcome():
    out = CliRunner().invoke(main, [])
    assert out.exit_code == 0
    assert "See it work in 10 seconds" in out.output
    assert "loophole demo" in out.output and "loophole init" in out.output


def test_empty_runs_and_ls_give_actionable_hint():
    db = os.path.join(tempfile.mkdtemp(prefix="loophole_fr_"), "x.db")
    for cmd in (["runs", "--db", db], ["ls", "--db", db]):
        out = CliRunner().invoke(main, cmd)
        assert out.exit_code == 0
        assert "no runs yet" in out.output and "loophole demo" in out.output


def test_subcommand_still_runs_not_welcome():
    # invoke_without_command must not swallow real subcommands
    out = CliRunner().invoke(main, ["executor", "list"])
    assert out.exit_code == 0 and "See it work in 10 seconds" not in out.output
