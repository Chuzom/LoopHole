#!/usr/bin/env python3
"""The "can't-fake-done" demo (STRAT-3).

The whole point of loophole in one runnable script: an LLM saying "done" is
worthless; only a contract's verifier can grant completion. A naive agent declares
success on buggy code, loophole REFUSES because the hard verifier fails, then
loophole accepts only once the code is actually fixed. No LLM required.

The logic now lives in the package (``loophole.demo``) so it's also available as a
first-class command:  ``loophole demo``  (add ``--slow`` for a dramatic pace).

    python examples/cant_fake_done.py     # or:  loophole demo
"""
from loophole.demo import run_demo

if __name__ == "__main__":
    run_demo(verbose=True)
