"""Privilege tiers + the guarded-action audit."""
import json
import os
import subprocess
import tempfile

import pytest
from click.testing import CliRunner

from loophole.cli import main
from loophole.loop import HumanUnavailable
from loophole.provider import Provider, Completion
from loophole.guarded import classify_guarded_action
from loophole.loop import privilege_preset
from loophole.contract import GoalContract, PRIVILEGE_TIERS, ContractError
from loophole.report import collect_guarded_actions, residual_risk_report
from loophole.audit import render_audit
from loophole.state import Store


# ---- guarded-action classifier ---------------------------------------------

@pytest.mark.parametrize("cmd,cat", [
    ("git push origin main", "vcs-push"),
    ("/usr/bin/git push --force", "vcs-push"),
    ("sudo git push", "vcs-push"),
    ("FOO=bar git push", "vcs-push"),
    ("npm publish", "publish"),
    ("twine upload dist/*", "publish"),
    ("python3 -m twine upload dist/*", "publish"),
    ("gh release create v1.0.0", "release"),
    ("docker push img:latest", "deploy"),
    ("kubectl apply -f k8s/", "deploy"),
    ("terraform apply -auto-approve", "deploy"),
    ("aws s3 sync ./build s3://b", "deploy"),
    ("stripe charges create", "purchase"),
    ("make build && git push", "vcs-push"),
])
def test_classify_guarded_positive(cmd, cat):
    assert classify_guarded_action(cmd) == cat


@pytest.mark.parametrize("cmd", [
    "git status", "git commit -m x", "npm install", "docker build -t x .",
    "kubectl get pods", "terraform plan", "aws s3 ls", "gh pr list",
    "pytest -q", "ls -la", "cargo build", "helm template ./c",
])
def test_classify_guarded_negative(cmd):
    assert classify_guarded_action(cmd) is None


# ---- tier presets -----------------------------------------------------------

def test_privilege_preset_full_and_guarded_are_non_forcing():
    for tier in ("full", "guarded"):
        p = privilege_preset(tier)
        assert p == {"force_sandbox": False, "deny_network": False, "require_human": False}


def test_privilege_preset_locked_forces_confinement():
    p = privilege_preset("locked")
    assert p["force_sandbox"] and p["deny_network"] and p["require_human"]


# ---- contract field ---------------------------------------------------------

def test_privilege_tier_defaults_to_guarded_and_round_trips():
    c = GoalContract.quick(goal="g", verify_cmd="true")
    assert c.privilege_tier == "guarded"
    c.privilege_tier = "locked"
    c.validate()
    assert GoalContract.from_json(c.to_json()).privilege_tier == "locked"


def test_invalid_privilege_tier_rejected():
    c = GoalContract.quick(goal="g", verify_cmd="true")
    c.privilege_tier = "root"
    with pytest.raises(ContractError):
        c.validate()
    assert set(PRIVILEGE_TIERS) == {"full", "guarded", "locked"}


# ---- guarded-action audit: store -> collect -> report/audit -----------------

def _store_with_guarded():
    db = os.path.join(tempfile.mkdtemp(prefix="loophole_priv_"), "s.db")
    st = Store(db)
    gid = st.create_goal(GoalContract.quick(goal="ship", verify_cmd="true").to_json(), "/tmp")
    st.log("guarded_action", goal_id=gid,
           payload={"command": "git push --force origin main", "category": "vcs-push"})
    st.log("guarded_action", goal_id=gid,
           payload={"command": "twine upload dist/*", "category": "publish"})
    return st, gid


def test_collect_guarded_actions_decodes_events():
    st, gid = _store_with_guarded()
    ga = collect_guarded_actions(st, gid)
    assert [g["category"] for g in ga] == ["vcs-push", "publish"]
    assert ga[0]["command"] == "git push --force origin main"


def test_report_lists_guarded_actions_for_guarded_tier():
    st, gid = _store_with_guarded()
    ga = collect_guarded_actions(st, gid)
    c = GoalContract.quick(goal="ship", verify_cmd="true")
    c.privilege_tier = "guarded"
    rep = residual_risk_report(c, None, "done", 1, "spent $0", None, guarded_actions=ga)
    assert "Guarded actions taken (audit)" in rep
    assert "twine upload dist/*" in rep


def test_report_full_tier_points_to_trail_without_listing():
    st, gid = _store_with_guarded()
    ga = collect_guarded_actions(st, gid)
    c = GoalContract.quick(goal="ship", verify_cmd="true")
    c.privilege_tier = "full"
    rep = residual_risk_report(c, None, "done", 1, "spent $0", None, guarded_actions=ga)
    assert "loophole audit" in rep
    assert "twine upload dist/*" not in rep  # not listed at full tier


def test_audit_renders_guarded_action():
    st, gid = _store_with_guarded()
    out = render_audit(st.get_goal(gid), st.events(gid), "ship")
    assert "guarded action [vcs-push]" in out
    assert "guarded action [publish]" in out


# ---- locked tier: headless completion pauses fail-closed (no silent done) ----

class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_priv_ws_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def test_locked_tier_pauses_for_human_when_headless(monkeypatch, tmp_path):
    """A hard verifier passing must NOT silently complete a 'locked' mission — with no
    human reachable (CliRunner has no TTY / no input) it PAUSES fail-closed."""
    tasks = [{"id": "t1", "description": "create add.py", "depends_on": [],
              "reads": [], "writes": ["add.py"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    # No human reachable: the checkpoint signals HumanUnavailable (as it does on EOF /
    # non-interactive stdin), so the loop must PAUSE fail-closed instead of completing.
    def _no_human(prompt):
        raise HumanUnavailable(prompt)
    monkeypatch.setattr("loophole.cli._human_checkpoint", _no_human)
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    write_add = "python3 -c \"open('add.py','w').write('def add(a,b): return a+b')\""
    verify = 'python3 -c "from add import add; assert add(2,3)==5"'
    r = CliRunner().invoke(main, [
        "run", "implement add", "--verify", verify, "--workspace", ws,
        "--executor-command", write_add, "--skip-critique", "--no-watch",
        "--max-rounds", "2", "--db", db, "--privileges", "locked",
    ])
    assert r.exit_code == 1, r.output          # paused = not done = exit 1
    assert "PAUSED" in r.output.upper()
    assert "human sign-off" in r.output.lower()
