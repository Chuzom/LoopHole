"""loophole command-line interface."""

from __future__ import annotations

import json
import os
import sys
from typing import Callable, List, Optional

import click

from . import __version__
from .budget import Budget, estimate as estimate_cost
from .contract import (GoalContract, Verifier, VerifierKind, ContractError,
                       http_check_command)
from . import registry
from .initializer import (CONTRACT_FILENAME, detect_contract, write_starter,
                          load_contract, load_template_raw,
                          list_templates as _list_templates)
from .audit import render_audit, render_runs
from .watch import render_frame, run_watch
from .loop import Roles, LoopConfig, run_goal
from .provider import make_provider, ProviderError
from .report import residual_risk_report
from .state import Store


class UsageError(click.ClickException):
    """A pre-run configuration/usage problem (bad contract, no goal, bad provider
    spec) — distinct from a run that executed but didn't reach 'done'.

    CI exit codes for `loophole run`:
      0 = verified done
      1 = not done (paused / failed / budget exhausted) — the run EXECUTED
      2 = usage/config error — the run never started
    """
    exit_code = 2


def _default_db() -> str:
    home = os.path.join(os.path.expanduser("~"), ".loophole")
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "loophole.db")


def _say(msg: str) -> None:
    click.echo(click.style("• ", fg="cyan") + msg)


def _no_runs_hint() -> str:
    return (click.style("no runs yet.", fg="bright_black") + " Try "
            + click.style("loophole demo", fg="cyan") + " or "
            + click.style('loophole run "<goal>" --verify "pytest -q"', fg="cyan"))


def _welcome() -> None:
    """Friendly first-run orientation shown on a bare `loophole`."""
    b = lambda s: click.style(s, bold=True)
    c = lambda s: click.style(s, fg="cyan")
    d = lambda s: click.style(s, fg="bright_black")
    click.echo()
    click.echo("  ⚒  " + b("loophole") + " — a swarm of agents that works until a check "
               + b("proves") + " the goal is done.")
    click.echo(d("     (a check decides \"done\" — never the agent.)"))
    click.echo()
    click.echo("  " + b("See it work in 10 seconds") + d("  (no setup, no API key):"))
    click.echo("    " + c("loophole demo") + d("          a naive agent says \"Done!\" on "
               "buggy code — loophole refuses, then accepts once it's fixed"))
    click.echo("    " + c("loophole watch --demo") + d("  watch the swarm work live in your terminal"))
    click.echo()
    click.echo("  " + b("Start on your own goal:"))
    click.echo("    " + c("loophole init") + d("          scaffold a loophole.json (infers a starter from your repo)"))
    click.echo("    " + c("loophole run \"<goal>\" --verify \"pytest -q\""))
    click.echo()
    click.echo(d("  ") + c("loophole --help") + d("  all commands   ·   ")
               + c("loophole stats") + d("  your value scorecard"))
    click.echo()


@click.group(invoke_without_command=True)
@click.version_option(__version__, prog_name="loophole")
@click.pass_context
def main(ctx: click.Context) -> None:
    """loophole — a swarm of agents that work until an acceptance contract passes."""
    if ctx.invoked_subcommand is None:
        _welcome()


def _demo_full(interval: float = 0.9, out=None) -> None:
    """Full-scale demo: a scripted multi-agent swarm (no LLM), rendered as the live
    milestone stream and closed with the value scorecard — the whole operating UX
    in one command."""
    import tempfile
    from .serve_demo import seed_and_simulate
    from .stream import stream_run
    from .scorecard import render_scorecard, run_scorecard
    out = out or sys.stdout
    store = Store(os.path.join(tempfile.mkdtemp(prefix="loophole_demo_"), "d.db"))
    goals, stop = seed_and_simulate(store, interval=interval)
    live = goals[2]
    try:
        stream_run(store, live, out=out, interval=min(interval, 0.4))  # until DONE
    except KeyboardInterrupt:
        pass
    stop.set()
    color = out.isatty() if hasattr(out, "isatty") else False
    out.write(render_scorecard(run_scorecard(store, live), color=color) + "\n")
    store.close()


@main.command()
@click.option("--slow", is_flag=True, help="Pause between acts for a dramatic pace.")
@click.option("--full", is_flag=True,
              help="Full-scale tour: a live swarm in THE FORGE + scorecard (no LLM/setup).")
def demo(slow: bool, full: bool) -> None:
    """Run the 30-second 'can't-fake-done' demo — no LLM, fully deterministic.

    A naive agent claims done on buggy code; loophole refuses (the verifier fails);
    after the bug is fixed, loophole accepts. The clearest one-command proof of the
    whole idea. Use --full for the live multi-agent swarm (THE FORGE) + scorecard.
    """
    if full:
        _demo_full()
        return
    from .demo import run_demo
    buggy_ok, fixed_ok = run_demo(verbose=True, pause=0.9 if slow else 0.0)
    # exit non-zero only if the invariant is somehow violated (defensive)
    sys.exit(0 if (not buggy_ok and fixed_ok) else 1)


@main.command()
@click.option("--path", "repo", default=".", help="Repo to inspect.")
@click.option("--force", is_flag=True, help="Overwrite an existing loophole.json.")
@click.option("--template", "template", default=None,
              help="Scaffold from a bundled template instead of inspecting the repo.")
@click.option("--list-templates", is_flag=True, help="List bundled templates and exit.")
@click.option("--goal", "goal", default=None,
              help="Set the goal now (skips the TODO placeholder / interactive prompt).")
@click.option("--ci", "ci", type=click.Choice(["github-actions", "gitlab"]), default=None,
              help="Also write a CI acceptance-gate workflow for this provider.")
@click.option("--from-ci", "from_ci", is_flag=True,
              help="Infer the verifier from the repo's OWN GitHub Actions workflows "
                   "(ground truth — the real CI command) instead of guessing from "
                   "file presence. Falls back to the usual heuristic if no workflow "
                   "is found or none of its steps look like a test command.")
def init(repo: str, force: bool, template: Optional[str], list_templates: bool,
         goal: Optional[str], ci: Optional[str], from_ci: bool) -> None:
    """Infer a starter contract (loophole.json) from the repo, or scaffold a template.

    Inspects the filesystem only — no code execution, no model calls. Pass --goal to
    fill it in immediately (or you'll be prompted in an interactive terminal).
    """
    if list_templates:
        for name in _list_templates():
            click.echo(name)
        return
    repo = os.path.realpath(repo)
    out = os.path.join(repo, CONTRACT_FILENAME)
    if os.path.exists(out) and not force:
        raise click.ClickException(
            "{} already exists (use --force to overwrite)".format(CONTRACT_FILENAME))
    if template:
        try:
            raw = registry.resolve(template)   # bundled template OR a registry entry
        except ValueError as e:
            raise click.ClickException(str(e))
        with open(out, "w", encoding="utf-8") as f:
            f.write(raw if raw.endswith("\n") else raw + "\n")
        _say("scaffolded from template '{}'".format(template))
    else:
        contract, notes = detect_contract(repo, from_ci=from_ci)
        write_starter(contract, out)
        for n in notes:
            _say(n)
    # Fill in the goal: explicit --goal, else prompt in an interactive terminal,
    # else leave the TODO placeholder (run refuses to launch on a TODO goal).
    resolved_goal = goal
    if not resolved_goal and sys.stdin.isatty():
        entered = click.prompt("Describe the goal in one sentence (what 'done' means)",
                               default="", show_default=False)
        resolved_goal = entered.strip() or None
    if resolved_goal:
        with open(out, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["goal"] = resolved_goal
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2) + "\n")
    if ci:
        ci_path = _write_ci_workflow(repo, ci)
        _say("wrote CI acceptance gate: {}".format(ci_path))
    click.echo("wrote " + click.style(out, fg="green"))
    if resolved_goal:
        click.echo("run it: " + click.style("loophole run --contract loophole.json", fg="cyan"))
    else:
        click.echo("edit the \"goal\" field, then: "
                   + click.style("loophole run --contract loophole.json", fg="cyan"))


_CI_WORKFLOWS = {
    "github-actions": (".github/workflows/loophole-gate.yml", """\
name: loophole-gate
on: [pull_request]
# pull-requests/checks write are only needed if you enable `comment: true`
# below (a sticky PR comment + an annotated Check Run with the verdict).
permissions:
  pull-requests: write
  checks: write
jobs:
  acceptance:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: Chuzom/loophole@v1
        with:
          contract: loophole.json
          comment: true
"""),
    "gitlab": (".gitlab-ci.yml", """\
loophole-gate:
  image: python:3.11
  script:
    - apt-get update && apt-get install -y bubblewrap
    - pip install loophole-agents
    - loophole contract validate loophole.json
    - loophole run --contract loophole.json --workspace .
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
"""),
}


def _write_ci_workflow(repo: str, provider: str) -> str:
    rel, content = _CI_WORKFLOWS[provider]
    dest = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(dest) or repo, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    return rel


_EXECUTOR_ADAPTERS = {
    "claude-code": "claude -p {task}",
    "codex": "codex exec {task} --json --sandbox workspace-write --skip-git-repo-check",
    "aider": "aider --yes-always --no-auto-commits --message {task}",
    "shell": "sh -c {task}",
}


@main.group()
def executor() -> None:
    """Bring-your-own executor adapters (run an external agent as the worker)."""


@executor.command("list")
def executor_list() -> None:
    """List executor backends — command templates + registered framework adapters."""
    click.echo("command templates (use with --executor-command):")
    for name, cmd in _EXECUTOR_ADAPTERS.items():
        click.echo("  {:14} loophole run --executor-command '{}'".format(name, cmd))
    from . import executors as _ex
    adapters = _ex.load_executors()
    click.echo("\nframework adapters (use with --executor <name>):")
    if adapters:
        for name in adapters:
            click.echo("  {:14} loophole run --executor {}".format(name, name))
    else:
        click.echo("  (none installed — pip-install a loophole executor adapter, or "
                   "register one via the `loophole.executors` entry point)")
    click.echo("\n{task} is replaced with the (shell-quoted) task description.")


@main.group("registry")
def registry_grp() -> None:
    """Shareable contract registry — named, reusable acceptance specs."""


@registry_grp.command("list")
def registry_list() -> None:
    """List resolvable contracts (bundled templates + your local registry)."""
    entries = registry.list_entries()
    if not entries:
        click.echo("(no entries)")
        return
    for e in entries:
        tag = "bundled" if e["source"] == "bundled" else click.style("local", fg="cyan")
        click.echo("{:28} [{}]".format(e["name"], tag))
    click.echo("\nuse: loophole run --contract <name>   or   loophole init --template <name>")


@registry_grp.command("add")
@click.argument("name")
@click.argument("src")
def registry_add(name: str, src: str) -> None:
    """Pull a contract from a PATH or URL into your local registry under NAME."""
    try:
        dest = registry.add(name, src)
    except (OSError, ValueError) as e:
        raise click.ClickException("could not add '{}': {}".format(name, e))
    click.echo("added " + click.style(name, fg="green") + " → " + dest)


@registry_grp.command("show")
@click.argument("name")
def registry_show(name: str) -> None:
    """Pretty-print a registry entry (resolves bundled or local)."""
    try:
        c = registry.get_contract(name)
    except (OSError, ValueError) as e:
        raise click.ClickException(str(e))
    click.echo("Goal: " + c.goal)
    for v in c.verifiers:
        click.echo("  - [{}] {}".format(v.kind.value, (v.command or v.rubric or v.prompt or "")[:80]))
    click.echo("allowed_writes:  " + ", ".join(c.allowed_writes))
    click.echo("protected_paths: " + ", ".join(c.all_protected_paths or ["(none)"]))


@registry_grp.command("remove")
@click.argument("name")
def registry_remove(name: str) -> None:
    """Remove a LOCAL registry entry (bundled templates are read-only)."""
    if registry.remove(name):
        click.echo("removed " + name)
    else:
        raise click.ClickException(
            "no local entry '{}' (bundled templates can't be removed)".format(name))


@registry_grp.command("add-source")
@click.argument("url")
def registry_add_source(url: str) -> None:
    """Subscribe to a remote index (a URL serving a name→contract manifest)."""
    registry.add_source(url)
    click.echo("source added: " + url)


@registry_grp.command("remove-source")
@click.argument("url")
def registry_remove_source(url: str) -> None:
    """Unsubscribe from a remote index."""
    if registry.remove_source(url):
        click.echo("source removed: " + url)
    else:
        raise click.ClickException("not a configured source: " + url)


@registry_grp.command("sources")
def registry_sources() -> None:
    """List configured remote index sources."""
    srcs = registry.list_sources()
    if not srcs:
        click.echo("(no remote sources — add one with `loophole registry add-source <url>`)")
    for s in srcs:
        click.echo(s)


@executor.command("test")
@click.argument("name")
def executor_test(name: str) -> None:
    """Smoke-check that an adapter's CLI is available on PATH."""
    import shutil as _sh
    if name not in _EXECUTOR_ADAPTERS:
        raise click.ClickException(
            "unknown adapter '{}'. Known: {}".format(name, ", ".join(_EXECUTOR_ADAPTERS)))
    cmd = _EXECUTOR_ADAPTERS[name]
    binary = cmd.split()[0]
    if _sh.which(binary):
        click.echo(click.style("ok", fg="green")
                   + " — '{}' found. Use: loophole run --executor-command '{}'".format(binary, cmd))
    else:
        raise click.ClickException(
            "'{}' not on PATH — install it or pick another adapter (loophole executor list)".format(binary))


@main.group()
def contract() -> None:
    """Work with Goal Contract files (acceptance-spec-as-code)."""


@contract.command("validate")
@click.argument("path")
def contract_validate(path: str) -> None:
    """Validate a contract file/URL (parses + satisfies the 'must define done' rule)."""
    try:
        c = load_contract(path)
        c.validate()
    except (OSError, ValueError, ContractError) as e:
        raise click.ClickException("invalid contract: {}".format(e))
    click.echo(click.style("valid", fg="green") + " — " + c.goal)


@contract.command("show")
@click.argument("path")
def contract_show(path: str) -> None:
    """Pretty-print a contract file/URL (verifiers, boundary, mutation policy)."""
    try:
        c = load_contract(path)
    except (OSError, ValueError) as e:
        raise click.ClickException("could not load contract: {}".format(e))
    click.echo("Goal: " + c.goal)
    click.echo("Verifiers:")
    for v in c.verifiers:
        detail = v.command or v.rubric or v.prompt or ""
        click.echo("  - [{}] {}".format(v.kind.value, detail[:80]))
    click.echo("allowed_writes:  " + ", ".join(c.allowed_writes))
    click.echo("protected_paths: " + ", ".join(c.all_protected_paths or ["(none)"]))


@main.command()
@click.argument("goal", required=False)
@click.option("--contract", "contract_path", default=None,
              help="Load the contract from a file (e.g. loophole.json from `init`).")
@click.option("--verify", "verify_cmd", default=None,
              help="Hard verifier command (exit 0 = done), e.g. 'pytest -q'.")
@click.option("--human", is_flag=True, help="Add a human checkpoint at completion.")
@click.option("--workspace", default="./loophole-out", help="Directory agents work in.")
@click.option("--watch/--no-watch", "watch_live", default=None,
              help="Show the live run view during the run (default ON). "
                   "--no-watch prints a plain log instead.")
@click.option("--view", type=click.Choice(["stream", "forge", "log"]), default=None,
              help="Live view: 'stream' (default) = append-only milestone lines, "
                   "clean in any terminal, CI, or Claude Desktop; 'forge' = the "
                   "full-screen animated dashboard (needs a TTY); 'log' = plain log.")
@click.option("--json", "emit_json", is_flag=True,
              help="Print the machine-readable run result (see "
                   "loophole/schemas/run_result.schema.json) to stdout after the "
                   "human report. For CI/tooling — the Action and PR-comment "
                   "mode consume this.")
@click.option("--json-file", "json_file", default=None,
              help="Also write the --json result to this path.")
@click.option("--planner-model", default="chuzom:moderate",
              help="provider:model for planning (default routes via Chuzom; moderate "
                   "tier for reliable plans).")
@click.option("--executor-model", default="chuzom:complex",
              help="provider:model for execution (default routes via Chuzom).")
@click.option("--executor-command", default=None,
              help="Bring-your-own executor: run an external agent CLI instead of "
                   "the built-in ReAct loop, e.g. 'claude -p {task}'. The agent is "
                   "sandboxed and the verifier boundary still governs 'done'.")
@click.option("--executor", "executor_name", default=None,
              help="Use a registered framework ADAPTER as each swarm worker "
                   "(see `loophole executor list`). The verifier boundary is unchanged.")
@click.option("--executor-network", default=None,
              help="Comma-separated hosts the executor may reach (for API-calling "
                   "agents). ENFORCED on macOS: the jail is localhost-only and egress "
                   "tunnels through a host-allowlisted proxy; denials are audited. On "
                   "Linux/bwrap egress is still all-or-nothing (declared hosts are "
                   "audit-only). Filesystem stays confined either way.")
@click.option("--executor-secret", default=None,
              help="Comma-separated env vars to pass through to the executor "
                   "(e.g. ANTHROPIC_API_KEY); every other secret stays scrubbed.")
@click.option("--executor-sandboxed", is_flag=True,
              help="Force-confine a trusted framework adapter in the OS sandbox "
                   "(opt-out). A trusted adapter like claude-code otherwise runs "
                   "unsandboxed so it can use your subscription login; this re-confines "
                   "it (subscription auth via keychain will then be blocked).")
@click.option("--critic-model", default=None, help="provider:model for critique (default: planner).")
@click.option("--cheap-model", default=None, help="provider:model for budget auto-downgrade.")
@click.option("--max-parallel", default=4, type=int)
@click.option("--max-rounds", default=25, type=int)
@click.option("--max-cost", default=0.0, type=float, help="USD ceiling (0 = unlimited).")
@click.option("--max-tokens", default=0, type=int, help="Token ceiling (0 = unlimited).")
@click.option("--protect", multiple=True, help="Protected path glob (repeatable).")
@click.option("--expect-test-delta", default=None, type=int,
              help="Min test-count change vs baseline (anti reward-hacking).")
@click.option("--verify-http", default=None, metavar="URL",
              help="Add a health-check hard verifier: does URL return "
                   "--verify-http-status (default 200)? Composable with --verify — "
                   "both must pass. Retries a few times a few seconds apart by "
                   "default (a just-started service, not a bug to work around by hand). "
                   "Unlike other verifiers, this one is network-ENABLED by design "
                   "(its whole purpose is a network call) — every other sandbox "
                   "confinement still applies.")
@click.option("--verify-http-status", default=200, type=int,
              help="Expected HTTP status for --verify-http (default 200).")
@click.option("--verify-http-timeout", default=5, type=int,
              help="Per-attempt timeout in seconds for --verify-http (default 5).")
@click.option("--verify-http-retries", default=3, type=int,
              help="Attempts for --verify-http before giving up (default 3).")
@click.option("--verify-http-retry-delay", default=2, type=int,
              help="Seconds between --verify-http retry attempts (default 2).")
@click.option("--skip-critique", is_flag=True, help="Skip plan critic + verifier adversary.")
@click.option("--comment", is_flag=True,
              help="Post/update a sticky PR comment with the Residual-Risk Report and "
                   "create a Check Run (annotated with any write-allowlist violations). "
                   "Requires running inside a GitHub Actions pull_request job with "
                   "GITHUB_TOKEN set (checks:write, pull-requests:write permissions).")
@click.option("--db", default=None, help="State DB path.")
def run(goal: Optional[str], contract_path: Optional[str], verify_cmd: Optional[str],
        human: bool, workspace: str, watch_live: Optional[bool], view: Optional[str],
        emit_json: bool, json_file: Optional[str],
        planner_model: str, executor_model: str, executor_command: Optional[str],
        executor_name: Optional[str], executor_network: Optional[str],
        executor_secret: Optional[str], executor_sandboxed: bool,
        critic_model: Optional[str],
        cheap_model: Optional[str], max_parallel: int, max_rounds: int,
        max_cost: float, max_tokens: int, protect: tuple,
        expect_test_delta: Optional[int],
        verify_http: Optional[str], verify_http_status: int, verify_http_timeout: int,
        verify_http_retries: int, verify_http_retry_delay: int,
        skip_critique: bool, comment: bool,
        db: Optional[str]) -> None:
    """Run a goal until its acceptance contract passes.

    Provide a GOAL with flags, or load a contract file with --contract. With no
    GOAL and a ./loophole.json present, that file is auto-loaded (from `init`).
    """
    # Auto-discover ./loophole.json when no goal and no explicit contract given.
    if contract_path is None and goal is None and os.path.exists(CONTRACT_FILENAME):
        contract_path = CONTRACT_FILENAME

    if contract_path:
        try:
            contract = registry.load_ref(contract_path)   # file, URL, or registry name
        except (OSError, ValueError) as e:
            raise UsageError(
                "could not load contract {}: {}".format(contract_path, e))
        if goal:                      # an explicit GOAL arg overrides the file's
            contract.goal = goal
        if contract.goal.strip().startswith("TODO"):
            raise UsageError(
                "the contract goal is still a TODO — edit {} and set a real goal"
                .format(contract_path))
    else:
        if not goal:
            raise UsageError(
                "provide a GOAL, or run `loophole init` then "
                "`loophole run --contract loophole.json`")
        verifiers: List[Verifier] = []
        if verify_cmd:
            verifiers.append(Verifier(
                kind=VerifierKind.HARD, command=verify_cmd,
                protected_paths=list(protect), expected_test_delta=expect_test_delta))
        if verify_http:
            try:
                http_cmd = http_check_command(
                    verify_http, status=verify_http_status, timeout=verify_http_timeout,
                    retries=verify_http_retries, retry_delay=verify_http_retry_delay)
            except ValueError as e:
                raise UsageError("--verify-http: {}".format(e))
            # Verifiers run network-denied by default (S1) — this one's entire
            # purpose is a network call, so it must opt in explicitly or curl
            # can never reach anything, even localhost.
            verifiers.append(Verifier(kind=VerifierKind.HARD, command=http_cmd,
                                      protected_paths=list(protect), allow_network=True))
        if human or not (verify_cmd or verify_http):
            verifiers.append(Verifier(kind=VerifierKind.HUMAN,
                                      prompt="Does the result satisfy: " + goal + "?"))
        contract = GoalContract(goal=goal, verifiers=verifiers,
                                protected_paths=list(protect),
                                max_cost_usd=max_cost, max_tokens=max_tokens,
                                max_rounds=max_rounds)
    try:
        contract.validate()
    except ContractError as e:
        raise UsageError(str(e))

    try:
        roles = Roles(
            planner=make_provider(planner_model),
            executor=make_provider(executor_model),
            critic=make_provider(critic_model or planner_model),
            cheap=make_provider(cheap_model) if cheap_model else None,
        )
    except ProviderError as e:
        raise UsageError(str(e))

    gh_ctx = None
    if comment:
        from .gh import pr_context
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            raise UsageError("--comment requires GITHUB_TOKEN in the environment "
                             "(the default token in a GitHub Actions job works).")
        gh_ctx = pr_context()
        if gh_ctx is None:
            raise UsageError("--comment must run inside a GitHub Actions "
                             "pull_request-triggered job (GITHUB_REPOSITORY / "
                             "GITHUB_EVENT_PATH not found or not a pull_request event).")

    workspace = os.path.realpath(workspace)
    os.makedirs(workspace, exist_ok=True)
    store = Store(db or _default_db())
    goal_id = store.create_goal(contract.to_json(), workspace)
    click.echo(click.style("goal ", fg="green") + goal_id)
    click.echo("workspace: " + workspace)

    budget = Budget(max_cost_usd=contract.max_cost_usd, max_tokens=contract.max_tokens)
    _csv = lambda s: tuple(x.strip() for x in s.split(",") if x.strip()) if s else ()
    cfg = LoopConfig(max_parallel=max_parallel, skip_plan_critique=skip_critique,
                     on_human=_human_checkpoint if contract.human_verifiers else None,
                     executor_command=executor_command, executor_name=executor_name,
                     executor_network=_csv(executor_network),
                     executor_secrets=_csv(executor_secret),
                     executor_sandboxed=executor_sandboxed)

    # The default live view is the append-only 'stream' — one clean milestone line
    # per real event, readable in any terminal, when piped, in CI, or in Claude
    # Desktop. 'forge' is the full-screen animated dashboard (TTY only); 'log' is a
    # plain scrolling log. --no-watch selects 'log'.
    if view is None:
        view = "stream" if (watch_live is None or watch_live) else "log"
    if view == "forge" and not sys.stdout.isatty():
        _say("(the forge dashboard needs an interactive terminal; using the stream view)")
        view = "stream"
    _run = lambda log: run_goal(store, goal_id, contract, roles, budget, cfg, log=log)
    if view == "stream":
        from .stream import stream_run
        outcome = stream_run(store, goal_id,
                             run_callable=lambda: _run(lambda _m: None))
    elif view == "forge":
        from .watch import watch_during
        outcome = watch_during(store, goal_id, lambda: _run(lambda _m: None))
    else:  # log
        outcome = _run(_say)

    click.echo()
    click.echo(residual_risk_report(
        contract, outcome.verdict, outcome.status, outcome.rounds,
        outcome.budget.summary(), outcome.verifier_bypasses, detail=outcome.detail))
    from .scorecard import run_scorecard, render_scorecard
    click.echo()
    click.echo(render_scorecard(run_scorecard(store, goal_id),
                                color=sys.stdout.isatty()))
    if emit_json or json_file or comment:
        from .report import to_json
        result = to_json(contract, outcome, store, goal_id)
        if emit_json:
            click.echo()
            click.echo(json.dumps(result, indent=2))
        if json_file:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
                f.write("\n")
        if comment:
            _post_pr_feedback(gh_ctx, result, _say)
    store.close()
    sys.exit(0 if outcome.status == "done" else 1)


def _post_pr_feedback(gh_ctx: dict, result: dict, say: Callable[[str], None]) -> None:
    """Post the sticky PR comment + Check Run. Best-effort AFTER the run has
    already completed: a network/API failure here must never change the run's
    own exit code — it only means the trust artifact didn't make it to GitHub."""
    from .gh import (GhError, annotations_from_boundary_events, create_check_run,
                     find_leaked_secrets, render_comment_body, upsert_pr_comment)
    token = os.environ["GITHUB_TOKEN"]     # presence already verified pre-run
    body = render_comment_body(result)
    leaked = find_leaked_secrets(body) or find_leaked_secrets(json.dumps(result))
    if leaked:
        say("--comment: refusing to post — output looks like it contains a "
            "secret value ({}). This should never happen (verifiers run with "
            "secrets scrubbed); investigate before re-running.".format(", ".join(leaked)))
        return
    try:
        upsert_pr_comment(gh_ctx["repo"], gh_ctx["pr_number"], token, body)
        say("posted PR comment on #{}".format(gh_ctx["pr_number"]))
    except GhError as e:
        say("--comment: could not post PR comment ({})".format(e))
    try:
        annotations = annotations_from_boundary_events(result.get("boundary_events", []))
        create_check_run(
            gh_ctx["repo"], gh_ctx["sha"], token, name="loophole / acceptance",
            conclusion="success" if result.get("verified_done") else "failure",
            title="{} — {}".format(result.get("status", "unknown").upper(),
                                   result.get("goal", "")[:80]),
            summary=result.get("residual_risk_text", ""), annotations=annotations)
        say("posted Check Run for {}".format(gh_ctx["sha"][:12]))
    except GhError as e:
        say("--comment: could not create Check Run ({})".format(e))


def _human_checkpoint(prompt: str) -> bool:
    return click.confirm(click.style("[human checkpoint] ", fg="yellow") + prompt, default=True)


@main.command()
@click.argument("goal")
@click.option("--verify", "verify_cmd", default=None)
@click.option("--max-rounds", default=25, type=int)
@click.option("--price-in", default=0.003, type=float)
@click.option("--price-out", default=0.015, type=float)
@click.option("--db", default=None)
def estimate(goal: str, verify_cmd: Optional[str], max_rounds: int,
             price_in: float, price_out: float, db: Optional[str]) -> None:
    """Dry-run cost estimate for a goal (no model calls).

    Grounded in the median per-round spend of past runs when history exists;
    otherwise a rough fixed heuristic (the output says which)."""
    store = Store(db or _default_db())
    est = estimate_cost(goal, rounds=max_rounds, price_in=price_in,
                        price_out=price_out, store=store)
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
    detail = g["detail"] if "detail" in g.keys() else None
    if detail and g["status"] in ("paused", "failed"):
        click.echo(click.style("why: ", fg="yellow") + detail)
        click.echo(click.style(
            "next: loophole resume {gid}   ·   loophole audit {gid}".format(gid=goal_id),
            dim=True))
    click.echo("")
    for t in store.tasks_for_goal(goal_id):
        color = {"done": "green", "failed": "red", "running": "yellow"}.get(t.status, "white")
        dep = (" <- " + ",".join(t.depends_on)) if t.depends_on else ""
        click.echo("  " + click.style("[{}]".format(t.status), fg=color) +
                   " " + t.description[:70] + dep)
    store.close()


@main.command()
@click.argument("goal_id", required=False)
@click.option("--db", default=None)
@click.option("--once", is_flag=True, help="Render a single frame and exit.")
@click.option("--interval", default=1.0, type=float, help="Refresh seconds.")
@click.option("--demo", is_flag=True, help="Self-driving live demo (no LLM, no real run).")
@click.option("--view", type=click.Choice(["stream", "forge"]), default="stream",
              help="'stream' (default) = append-only milestone lines; "
                   "'forge' = the full-screen animated dashboard.")
def watch(goal_id: Optional[str], db: Optional[str], once: bool, interval: float,
          demo: bool, view: str) -> None:
    """Follow a run live — the append-only milestone stream (or --view forge for the
    animated dashboard: agents, merge-train, and the VERIFY GATE).

    `--demo` runs a self-driving swarm so you can watch the UI without an LLM or a
    real run.
    """
    if demo:
        import tempfile
        store = Store(os.path.join(tempfile.mkdtemp(prefix="loophole_demo_"), "demo.db"))
        from .serve_demo import seed_and_simulate
        goals, _stop = seed_and_simulate(store, interval=1.4)
        goal_id = goals[2]                       # the live, animating run
        interval = min(interval, 0.3)
    else:
        store = Store(db or _default_db())
        if not goal_id:
            store.close()
            raise click.ClickException("a GOAL_ID is required (or use --demo)")
        if not store.get_goal(goal_id):
            store.close()
            raise click.ClickException("no such goal: " + goal_id)
    try:
        if view == "stream" and not once:
            from .stream import stream_run
            stream_run(store, goal_id, interval=min(interval, 0.4))
        else:
            run_watch(store, goal_id, interval=interval, once=once)
    except KeyboardInterrupt:
        click.echo("")
    finally:
        store.close()


@main.command()
@click.argument("goal_id", required=False)
@click.option("--db", default=None)
@click.option("--host", default="127.0.0.1", help="Bind host.")
@click.option("--port", default=8765, type=int, help="Port (0 = pick a free one).")
@click.option("--no-browser", is_flag=True, help="Don't auto-open a browser.")
@click.option("--demo", is_flag=True, help="Self-driving live demo (no LLM, no real run).")
def serve(goal_id: Optional[str], db: Optional[str], host: str, port: int,
          no_browser: bool, demo: bool) -> None:
    """Live WEB view of a run — THE FORGE in the browser (Phase 1).

    With no GOAL_ID, opens the FLEET view (all runs, click a card to drill in). Pass
    a GOAL_ID to open that run's Forge directly. `--demo` spins up a self-driving
    fleet so you can watch the UI animate without an LLM or a real run.
    """
    from .serve import serve as _serve
    if demo:
        import tempfile
        store = Store(os.path.join(tempfile.mkdtemp(prefix="loophole_demo_"), "demo.db"))
        from .serve_demo import seed_and_simulate
        seed_and_simulate(store)
        click.echo("demo mode — self-driving fleet (no LLM).")
        goal_id = None
    else:
        store = Store(db or _default_db())
        if goal_id and not store.get_goal(goal_id):
            store.close()
            raise click.ClickException("no such goal: " + goal_id)
    try:
        _serve(store, goal_id, host=host, port=port, open_browser=not no_browser)
    finally:
        store.close()


@main.command()
@click.argument("goal_id")
@click.option("--db", default=None)
def audit(goal_id: str, db: Optional[str]) -> None:
    """Show the full audit trail for a run (every boundary decision, with reasons)."""
    store = Store(db or _default_db())
    g = store.get_goal(goal_id)
    if not g:
        raise click.ClickException("no such goal: " + goal_id)
    goal_text = GoalContract.from_json(g["contract"]).goal
    click.echo(render_audit(g, store.events(goal_id), goal_text))
    store.close()


@main.command()
@click.option("--db", default=None)
def runs(db: Optional[str]) -> None:
    """List past runs with their outcome."""
    store = Store(db or _default_db())
    goals = store.list_goals()
    if not goals:
        click.echo(_no_runs_hint())
    else:
        click.echo(render_runs(goals, lambda g: GoalContract.from_json(g["contract"]).goal))
    store.close()


@main.command()
@click.option("--db", default=None)
def stats(db: Optional[str]) -> None:
    """Your LoopHole value scorecard across all runs — verified, rejections, cheats blocked."""
    from .scorecard import aggregate, render_aggregate
    store = Store(db or _default_db())
    try:
        click.echo(render_aggregate(aggregate(store), color=sys.stdout.isatty()))
    finally:
        store.close()


@main.command(name="ls", hidden=True)
@click.option("--db", default=None)
@click.pass_context
def ls(ctx: click.Context, db: Optional[str]) -> None:
    """Alias of `runs` (kept for muscle memory)."""
    ctx.invoke(runs, db=db)


@main.command()
@click.argument("goal_id")
@click.option("--executor-model", default="chuzom:complex")
@click.option("--planner-model", default="chuzom:moderate")
@click.option("--json", "emit_json", is_flag=True,
              help="Print the machine-readable run result after the human report.")
@click.option("--json-file", "json_file", default=None,
              help="Also write the --json result to this path.")
@click.option("--db", default=None)
def resume(goal_id: str, executor_model: str, planner_model: str,
          emit_json: bool, json_file: Optional[str], db: Optional[str]) -> None:
    """Resume a paused/failed goal (discards un-merged worktrees, re-runs pending)."""
    store = Store(db or _default_db())
    g = store.get_goal(goal_id)
    if not g:
        raise UsageError("no such goal: " + goal_id)
    contract = GoalContract.from_json(g["contract"])
    store.set_goal_status(goal_id, "running")
    try:
        roles = Roles(planner=make_provider(planner_model),
                      executor=make_provider(executor_model),
                      critic=make_provider(planner_model))
    except ProviderError as e:
        raise UsageError(str(e))
    budget = Budget(max_cost_usd=contract.max_cost_usd, max_tokens=contract.max_tokens)
    cfg = LoopConfig()
    outcome = run_goal(store, goal_id, contract, roles, budget, cfg, log=_say)
    click.echo()
    click.echo(residual_risk_report(contract, outcome.verdict, outcome.status,
                                    outcome.rounds, outcome.budget.summary(),
                                    outcome.verifier_bypasses, detail=outcome.detail))
    if emit_json or json_file:
        from .report import to_json
        result = to_json(contract, outcome, store, goal_id)
        if emit_json:
            click.echo()
            click.echo(json.dumps(result, indent=2))
        if json_file:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
                f.write("\n")
    store.close()
    sys.exit(0 if outcome.status == "done" else 1)


@main.command()
def mcp() -> None:
    """Run loophole as an MCP server (stdio) — use it from Claude Code / Claude
    Desktop / any MCP client. Describe a goal, run it, and watch progress inside
    the session. Register with:  claude mcp add loophole -- loophole mcp
    """
    from .mcp_server import serve
    serve()


if __name__ == "__main__":
    main()
