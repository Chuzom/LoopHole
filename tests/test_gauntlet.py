"""Wires the reward-hacking gauntlet (gauntlet/scenarios.py) into the regular
test suite so a regression in the verifier boundary fails CI immediately,
not just when someone remembers to run ``python -m gauntlet`` by hand.
"""
from __future__ import annotations

import pytest

from gauntlet.scenarios import ALL_SCENARIOS, run_scenario


@pytest.mark.parametrize("scenario", ALL_SCENARIOS, ids=lambda sc: sc.key)
def test_gauntlet_scenario_is_caught(scenario):
    result = run_scenario(scenario)
    assert result.caught, (
        "gauntlet regression: '{}' was NOT caught (verified_done={}) — {}"
        .format(scenario.title, result.verified_done, result.detail)
    )
