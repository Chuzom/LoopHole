"""STRAT-3 / `loophole demo` — guard the can't-fake-done demo against bit-rot.

The demo IS the value proposition; if it ever stops refusing buggy code (or stops
accepting correct code), that's a regression worth a red test.
"""
from __future__ import annotations

from click.testing import CliRunner

from loophole.cli import main
from loophole.demo import run_demo


def test_loophole_refuses_buggy_accepts_fixed():
    buggy_accepted, fixed_accepted = run_demo(verbose=False)
    assert buggy_accepted is False   # the false "done" claim is caught
    assert fixed_accepted is True    # completion granted only for correct code


def test_cli_demo_command():
    r = CliRunner().invoke(main, ["demo"])
    assert r.exit_code == 0, r.output
    assert "can't-fake-done" in r.output
    assert "does NOT declare done" in r.output
    assert "accept completion" in r.output
