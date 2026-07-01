"""`loophole estimate` grounds itself in real run history when it exists."""

import os

from loophole.budget import estimate
from loophole.state import Store


def _store_with_runs(tmp_path, spends):
    store = Store(os.path.join(str(tmp_path), "state.db"))
    for spent_usd, spent_tokens, rounds in spends:
        gid = store.create_goal("{}", str(tmp_path))
        store.log("run_spend", goal_id=gid,
                  payload={"spent_usd": spent_usd, "spent_tokens": spent_tokens,
                           "rounds": rounds, "status": "done"})
    return store


def test_estimate_grounded_in_history(tmp_path):
    # medians: 1000 and 3000 tokens/round -> mid element of [1000, 3000] = 3000
    store = _store_with_runs(tmp_path, [(0.0, 2000, 2), (0.30, 9000, 3)])
    est = estimate("goal", rounds=10, store=store)
    assert est["basis"] == "history"
    assert est["estimated_tokens"] == 3000 * 10
    assert abs(est["estimated_cost_usd"] - 1.0) < 1e-6   # 0.10 usd/round * 10
    assert "2 past run(s)" in est["note"]


def test_estimate_falls_back_to_heuristic(tmp_path):
    # no store at all
    assert estimate("goal", rounds=10)["basis"] == "heuristic"
    # a store with no usable history (zero rounds/tokens)
    store = _store_with_runs(tmp_path, [(0.0, 0, 0)])
    est = estimate("goal", rounds=10, store=store)
    assert est["basis"] == "heuristic"
    assert est["estimated_tokens"] > 0
