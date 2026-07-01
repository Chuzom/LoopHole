"""Planner/critic calls are charged to the budget (previously only the
executor path charged, so ceilings didn't bind planning and run_spend
under-reported)."""

from loophole.budget import Budget
from loophole.provider import ChargingProvider, Completion, Provider


class _P(Provider):
    name = "p"
    price_in = 1.0     # $/1k in
    price_out = 2.0    # $/1k out

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text='{"ok": true}', prompt_tokens=1000,
                          completion_tokens=500)


def test_charging_provider_charges_budget():
    b = Budget()
    p = ChargingProvider(_P(), b)
    c = p.complete([])
    assert c.text == '{"ok": true}'
    assert b.spent_tokens == 1500
    assert abs(b.spent_usd - 2.0) < 1e-9      # 1.0*1 + 2.0*0.5
    assert p.name == "p"                       # attribute delegation


def test_charging_provider_in_run_goal_charges_planning(tmp_path):
    # a paused/failed run whose only LLM activity is planning must record spend
    import os
    from loophole.contract import GoalContract
    from loophole.budget import Budget as _Budget
    from loophole.loop import LoopConfig, Roles, run_goal
    from loophole.state import Store
    import subprocess
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(ws), check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=str(ws), check=True)
    store = Store(os.path.join(str(tmp_path), "s.db"))
    contract = GoalContract.quick("x", verify_cmd="false",   # verifier always fails
                                  max_rounds=1)
    gid = store.create_goal(contract.to_json(), str(ws))

    class _BadPlanner(_P):
        def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
            return Completion(text="not json", prompt_tokens=100, completion_tokens=10)

    roles = Roles(planner=_BadPlanner(), executor=_P(), critic=_P())
    out = run_goal(store, gid, contract, roles, budget=_Budget(), cfg=LoopConfig())
    assert out.status == "failed"              # planner never produced a DAG
    assert out.budget.spent_tokens > 0         # ...but its calls were charged
