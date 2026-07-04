"""Keeps README's test-count badge honest — the discipline the product sells.

A hand-authored badge drifts the moment someone adds or removes a test
without noticing; this turns that drift into a CI failure instead of a
silently stale claim (found stale by 93 tests during the 2026-07-04
competitive audit — the exact bug a "prove it, don't claim it" tool
shouldn't have in its own README).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BADGE_RE = re.compile(r"tests-(\d+)%20passing")
_COLLECTED_RE = re.compile(r"(\d+) tests? collected")
_GAUNTLET_BADGE_RE = re.compile(r"reward--hacking-(\d+)%2F(\d+)%20caught")


def test_readme_test_count_badge_matches_actual_collection():
    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = _BADGE_RE.search(readme)
    assert m, "README.md's tests badge line not found, or its shape changed"
    claimed = int(m.group(1))

    r = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=60,
    )
    cm = _COLLECTED_RE.search(r.stdout)
    assert cm, "could not parse pytest --collect-only's summary line:\n" + r.stdout[-500:]
    actual = int(cm.group(1))

    assert claimed == actual, (
        "README's tests badge claims {} passing but {} are actually collected "
        "— update the badge in README.md (the shields.io URL containing "
        "'tests-{}%20passing').".format(claimed, actual, claimed)
    )


def test_readme_gauntlet_badge_matches_scenario_count():
    from gauntlet.scenarios import ALL_SCENARIOS

    readme = (_REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = _GAUNTLET_BADGE_RE.search(readme)
    assert m, "README.md's reward-hacking gauntlet badge not found, or its shape changed"
    claimed_caught, claimed_total = int(m.group(1)), int(m.group(2))

    actual_total = len(ALL_SCENARIOS)
    assert claimed_total == actual_total, (
        "README's gauntlet badge claims {} total scenarios but gauntlet/scenarios.py "
        "defines {} — update the badge.".format(claimed_total, actual_total)
    )
    # the badge should never claim a caught-count higher than what test_gauntlet.py
    # actually enforces; if a scenario is ever allowed to fail, the badge must say so.
    assert claimed_caught == claimed_total, (
        "README's gauntlet badge claims {}/{} caught — if that's not 100%, the badge "
        "text is making an excuse, not reporting a result.".format(claimed_caught, claimed_total)
    )
