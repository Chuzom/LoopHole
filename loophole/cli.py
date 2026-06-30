"""loophole command-line interface."""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional

import click

from . import __version__
from .budget import Budget, estimate as estimate_cost
from .contract import GoalContract, Verifier, VerifierKind, ContractError
from .initializer import (CONTRACT_FILENAME, detect_contract, write_starter,
                          load_contract)
from .loop import Roles, LoopConfig, run_goal
from .provider import make_provider, ProviderError
from .report import residual_risk_report
from .state import Store


def _default_db() -> str:
    home = os.path.join(os.path.expanduser("~"), ".loophole")
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "loophole.db")


def _say(msg: str) -> None:
    click.echo(click.style("• ", fg="cyan") + msg)


@click.group()
@click.version_option(__version__, prog_name="loophole")
def main() -> None:
    """loophole — a swarm of agents that work until an acceptance contract passes."""


@main.command()
@click.option("--path", "repo", default=".", help="Repo to inspect.")
@click.option("--force", is_flag=True, help="Overwrite an existing loophole.json.")
def init(repo: str, force: bool) -> None:
    """Infer a starter contract (loophole.json) from the repo.

    Inspects the filesystem only — no code execution, no model calls. Edit the
    emitted 'goal' field, then run with --contract.
    """
    repo = os.path.realpath(repo)
    out = os.path.join(repo, CONTRACT_FILENAME)
    if os.path.exists(out) and not force:
        raise click.ClickException(
            "{} already exists (use --force to overwrite)".format(CONTRACT_FILENAME))
    contract, notes = detect_contract(repo)
    write_starter(contract, out)
    for n in notes:
        _say(n)
    click.echo("wrote " + click.style(out, fg="green"))
    click.echo("edit the \"goal\" field, then: "
               + click.style("loophole run --contract loophole.json", fg="cyan"))


@main.command()
@click.argument("goal", required=False)
@click.option("--contract", "contract_path", default=None,
              help="Load the contract from a file (e.g. loophole.json from `init`).")
@click.option("--verify", "verify_cmd", default=None,
              help="Hard verifier command (exit 0 = done), e.g. 'pytest -q'.")
@click.option("--human", is_flag=True, help="Add a human checkpoint at completion.")
@click.option("--workspace", default="./loophole-out", help="Directory agents work in.")
@click.option("--planner-model", default="ollama:llama3", help="provider:model for planning.")
@click.option("--executor-model", default="ollama:llama3", help="provider:model for execution.")
@click.option("--critic-model", default=None, help="provider:model for critique (default: planner).")
@click.option("--cheap-model", default=None, help="provider:model for budget auto-downgrade.")
@click.option("--max-parallel", default=4, type=int)
@click.option("--max-rounds", default=25, type=int)
@click.option("--max-cost", default=0.0, type=float, help="USD ceiling (0 = unlimited).")
@click.option("--max-tokens", default=0, type=int, help="Token ceiling (0 = unlimited).")
@click.option("--protect", multiple=True, help="Protected path glob (repeatable).")
@click.option("--expect-test-delta", default=None, type=int,
              help="Min test-count change vs baseline (anti reward-hacking).")
@click.option("--skip-critique", is_flag=True, help="Skip plan critic + verifier adversary.")
@click.option("--db", default=None, help="State DB path.")
def run(goal: Optional[str], contract_path: Optional[str], verify_cmd: Optional[str],
        human: bool, workspace: str,
        planner_model: str, executor_model: str, critic_model: Optional[str],
        cheap_model: Optional[str], max_parallel: int, max_rounds: int,
        max_cost: float, max_tokens: int, protect: tuple,
        expect_test_delta: Optional[int], skip_critique: bool, db: Optional[str]) -> None:
    """Run a goal until its acceptance contract passes.

    Provide a GOAL with flags, or load a contract file with --contract. With no
    GOAL and a ./loophole.json present, that file is auto-loaded (from `init`).
    """
    # Auto-discover ./loophole.json when no goal and no explicit contract given.
    if contract_path is None and goal is None and os.path.exists(CONTRACT_FILENAME):
        contract_path = CONTRACT_FILENAME

    if contract_path:
        try:
            contract = load_contract(contract_path)
        except (OSError, ValueError) as e:
            raise click.ClickException(
                "could not load contract {}: {}".format(contract_path, e))
        if goal:                      # an explicit GOAL arg overrides the file's
            contract.goal = goal
        if contract.goal.strip().startswith("TODO"):
            raise click.ClickException(
                "the contract goal is still a TODO — edit {} and set a real goal"
                .format(contract_path))
    else:
        if not goal:
            raise click.ClickException(
                "provide a GOAL, or run `loophole init` then "
                "`loophole run --contract loophole.json`")
        verifiers: List[Verifier] = []
        if verify_cmd:
            verifiers.append(Verifier(
                kind=VerifierKind.HARD, command=verify_cmd,
                protected_paths=list(protect), expected_test_delta=expect_test_delta))
        if human or not verify_cmd:
            verifiers.append(Verifier(kind=VerifierKind.HUMAN,
                                      prompt="Does the result satisfy: " + goal + "?"))
        contract = GoalContract(goal=goal, verifiers=verifiers,
                                protected_paths=list(protect),
                                max_cost_usd=max_cost, max_tokens=max_tokens,
                                max_rounds=max_rounds)
    try:
        contract.validate()
    except ContractError as e:
        raise click.ClickException(str(e))

    try:
        roles = Roles(
            planner=make_provider(planner_model),
            executor=make_provider(executor_model),
            critic=make_provider(critic_model or planner_model),
            cheap=make_provider(cheap_model) if cheap_model else None,
        )
    except ProviderError as e:
        raise click.ClickException(str(e))

    workspace = os.path.realpath(workspace)
    os.makedirs(workspace, exist_ok=True)
    store = Store(db or _default_db())
    goal_id = store.create_goal(contract.to_json(), workspace)
    click.echo(click.style("goal ", fg="green") + goal_id)
    click.echo("workspace: " + workspace)

    budget = Budget(max_cost_usd=contract.max_cost_usd, max_tokens=contract.max_tokens)
    cfg = LoopConfig(max_parallel=max_parallel, skip_plan_critique=skip_critique,
                     on_human=_human_checkpoint if contract.human_verifiers else None)

    outcome = run_goal(store, goal_id, contract, roles, budget, cfg, log=_say)

    click.echo()
    click.echo(residual_risk_report(
        contract, outcome.verdict, outcome.status, outcome.rounds,
        outcome.budget.summary(), outcome.verifier_bypasses))
    store.close()
    sys.exit(0 if outcome.status == "done" else 1)


def _human_checkpoint(prompt: str) -> bool:
    return click.confirm(click.style("[human checkpoint] ", fg="yellow") + prompt, default=True)


@main.command()
@click.argument("goal")
@click.option("--verify", "verify_cmd", default=None)
@click.option("--max-rounds", default=25, type=int)
@click.option("--price-in", default=0.003, type=float)
@click.option("--price-out", default=0.015, type=float)
def estimate(goal: str, verify_cmd: Optional[str], max_rounds: int,
             price_in: float, price_out: float) -> None:
    """Dry-run cost estimate for a goal (no model calls)."""
    est = estimate_cost(goal, rounds=max_rounds, price_in=price_in, price_out=price_out)
    click.echo(json.dumps(est, indent=2))


@main.command()
@click.argument("goal_id")
@click.option("--db", default=None)
def status(goal_id: str, db: Optional[str]) -> None:
    """Show a goal's task DAG and status."""
    store = Store(db or _default_db())
    g = store.get_goal(goal_id)
    if not g:
        raise click.ClickException("no such goal: " + goal_id)
    click.echo(click.style(goal_id, fg="green") + "  [" + g["status"] + "]")
    contract = GoalContract.from_json(g["contract"])
    click.echo("goal: " + contract.goal)
    click.echo("workspace: " + g["workspace"])
    click.echo("")
    for t in store.tasks_for_goal(goal_id):
        color = {"done": "green", "failed": "red", "running": "yellow"}.get(t.status, "white")
        dep = (" <- " + ",".join(t.depends_on)) if t.depends_on else ""
        click.echo("  " + click.style("[{}]".format(t.status), fg=color) +
                   " " + t.description[:70] + dep)
    store.close()


@main.command(name="ls")
@click.option("--db", default=None)
def ls(db: Optional[str]) -> None:
    """List all goals."""
    store = Store(db or _default_db())
    for g in store.list_goals():
        contract = GoalContract.from_json(g["contract"])
        color = {"done": "green", "failed": "red", "paused": "yellow"}.get(g["status"], "white")
        click.echo(click.style("{:8}".format(g["status"]), fg=color) + " " +
                   g["id"] + "  " + contract.goal[:60])
    store.close()


@main.command()
@click.argument("goal_id")
@click.option("--executor-model", default="ollama:llama3")
@click.option("--planner-model", default="ollama:llama3")
@click.option("--db", default=None)
def resume(goal_id: str, executor_model: str, planner_model: str, db: Optional[str]) -> None:
    """Resume a paused/failed goal (discards un-merged worktrees, re-runs pending)."""
    store = Store(db or _default_db())
    g = store.get_goal(goal_id)
    if not g:
        raise click.ClickException("no such goal: " + goal_id)
    contract = GoalContract.from_json(g["contract"])
    store.set_goal_status(goal_id, "running")
    try:
        roles = Roles(planner=make_provider(planner_model),
                      executor=make_provider(executor_model),
                      critic=make_provider(planner_model))
    except ProviderError as e:
        raise click.ClickException(str(e))
    budget = Budget(max_cost_usd=contract.max_cost_usd, max_tokens=contract.max_tokens)
    cfg = LoopConfig()
    outcome = run_goal(store, goal_id, contract, roles, budget, cfg, log=_say)
    click.echo()
    click.echo(residual_risk_report(contract, outcome.verdict, outcome.status,
                                    outcome.rounds, outcome.budget.summary(),
                                    outcome.verifier_bypasses))
    store.close()
    sys.exit(0 if outcome.status == "done" else 1)


if __name__ == "__main__":
    main()
