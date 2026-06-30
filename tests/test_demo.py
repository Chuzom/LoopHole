"""STRAT-3 — guard the 'can't-fake-done' demo against bit-rot.

The demo IS the value proposition; if it ever stops refusing buggy code (or stops
accepting correct code), that's a regression worth a red test.
"""
from __future__ import annotations

import importlib.util
import os


def _load_demo():
    path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..",
                                        "examples", "cant_fake_done.py"))
    spec = importlib.util.spec_from_file_location("cant_fake_done", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_loophole_refuses_buggy_accepts_fixed():
    mod = _load_demo()
    buggy_accepted, fixed_accepted = mod.run_demo(verbose=False)
    assert buggy_accepted is False   # the false "done" claim is caught
    assert fixed_accepted is True    # completion granted only for correct code
