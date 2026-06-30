"""Verifier/contract registry — resolution, add/remove, and CLI wiring."""
from __future__ import annotations

import json
import os
import tempfile

import pytest
from click.testing import CliRunner

from loophole import registry
from loophole.cli import main
from loophole.contract import GoalContract, Verifier, VerifierKind


@pytest.fixture()
def reg(monkeypatch):
    d = tempfile.mkdtemp(prefix="loophole_reg_")
    monkeypatch.setenv("LOOPHOLE_REGISTRY_DIR", d)
    return d


def _contract_file(goal="ship it"):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    f.write(GoalContract(goal=goal, verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json())
    f.close()
    return f.name


# ---- resolution -------------------------------------------------------------

def test_list_includes_bundled_templates(reg):
    names = {e["name"] for e in registry.list_entries()}
    assert {"python-lib", "refactor-frozen-tests", "human-reviewed"} <= names


def test_add_resolve_get_remove(reg):
    path = _contract_file("do the thing")
    dest = registry.add("myspec", path)
    assert os.path.dirname(dest) == reg
    assert {e["name"]: e["source"] for e in registry.list_entries()}["myspec"] == "local"
    assert registry.get_contract("myspec").goal == "do the thing"
    assert registry.remove("myspec") is True
    assert registry.remove("myspec") is False   # gone now


def test_resolve_unknown_raises(reg):
    with pytest.raises(ValueError):
        registry.resolve("nope-not-here")


def test_local_overrides_bundled(reg):
    path = _contract_file("my python-lib override")
    registry.add("python-lib", path)            # same name as a bundled template
    assert registry.get_contract("python-lib").goal == "my python-lib override"


def test_load_ref_name_path_and_url(reg):
    import pathlib
    path = _contract_file("by goal")
    registry.add("named", path)
    assert registry.load_ref("named").goal == "by goal"          # registry name
    assert registry.load_ref(path).goal == "by goal"             # file path
    url = pathlib.Path(path).as_uri()
    assert registry.load_ref(url).goal == "by goal"              # file:// URL


# ---- CLI --------------------------------------------------------------------

def test_cli_registry_add_list_show_remove(reg):
    runner = CliRunner()
    path = _contract_file("cli goal")
    r = runner.invoke(main, ["registry", "add", "cli-spec", path])
    assert r.exit_code == 0, r.output

    r = runner.invoke(main, ["registry", "list"])
    assert r.exit_code == 0 and "cli-spec" in r.output and "python-lib" in r.output

    r = runner.invoke(main, ["registry", "show", "cli-spec"])
    assert r.exit_code == 0 and "cli goal" in r.output and "pytest -q" in r.output

    r = runner.invoke(main, ["registry", "remove", "cli-spec"])
    assert r.exit_code == 0
    r = runner.invoke(main, ["registry", "remove", "python-lib"])   # bundled -> can't
    assert r.exit_code != 0


def test_cli_init_template_resolves_registry_name(reg):
    runner = CliRunner()
    path = _contract_file("registry-scaffolded goal")
    runner.invoke(main, ["registry", "add", "team-default", path])
    with runner.isolated_filesystem():
        r = runner.invoke(main, ["init", "--template", "team-default", "--goal", "x"])
        assert r.exit_code == 0, r.output
        assert json.load(open("loophole.json"))["goal"] == "x"


def test_cli_run_contract_unknown_name_errors_via_registry(reg):
    # `run --contract <name>` resolves through the registry; an unknown name surfaces
    # the registry error (proving the wiring), not a generic file error.
    r = CliRunner().invoke(main, ["run", "--contract", "does-not-exist-anywhere"])
    assert r.exit_code != 0
    assert "unknown registry entry" in r.output or "registry list" in r.output


# ---- remote index sources ---------------------------------------------------

def _write(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_remote_index_resolves_via_file_url(reg):
    import pathlib
    d = tempfile.mkdtemp(prefix="loophole_idx_")
    # a contract the index points at (relative URL, resolved against the index URL)
    _write(os.path.join(d, "web.json"), GoalContract(goal="serve a web app", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="pytest -q")]).to_json())
    # the index manifest: name -> relative URL, plus an inline contract
    _write(os.path.join(d, "index.json"), json.dumps({"entries": {
        "team-web": "web.json",
        "team-inline": json.loads(GoalContract(goal="inline goal", verifiers=[
            Verifier(kind=VerifierKind.HARD, command="true")]).to_json()),
    }}))
    index_url = pathlib.Path(os.path.join(d, "index.json")).as_uri()

    registry.add_source(index_url)
    assert index_url in registry.list_sources()
    names = {e["name"]: e["source"] for e in registry.list_entries()}
    assert names.get("team-web") == "remote" and names.get("team-inline") == "remote"
    # resolve fetches the contract (relative URL joined to the index)
    assert registry.get_contract("team-web").goal == "serve a web app"
    assert registry.get_contract("team-inline").goal == "inline goal"   # inline entry
    # run/init resolve it by name too
    assert registry.load_ref("team-web").goal == "serve a web app"

    assert registry.remove_source(index_url) is True
    assert "team-web" not in {e["name"] for e in registry.list_entries()}


def test_precedence_local_over_remote_over_bundled(reg):
    import pathlib
    d = tempfile.mkdtemp(prefix="loophole_idx2_")
    _write(os.path.join(d, "r.json"), GoalContract(goal="remote python-lib", verifiers=[
        Verifier(kind=VerifierKind.HARD, command="true")]).to_json())
    _write(os.path.join(d, "index.json"),
           json.dumps({"entries": {"python-lib": "r.json"}}))
    registry.add_source(pathlib.Path(os.path.join(d, "index.json")).as_uri())
    # remote overrides the bundled python-lib template
    assert registry.get_contract("python-lib").goal == "remote python-lib"
    # a local entry overrides the remote
    registry.add("python-lib", _contract_file("local python-lib"))
    assert registry.get_contract("python-lib").goal == "local python-lib"


def test_cli_sources_commands(reg):
    runner = CliRunner()
    r = runner.invoke(main, ["registry", "sources"])
    assert r.exit_code == 0 and "no remote sources" in r.output
    r = runner.invoke(main, ["registry", "add-source", "https://example.test/index.json"])
    assert r.exit_code == 0
    r = runner.invoke(main, ["registry", "sources"])
    assert "example.test" in r.output
    r = runner.invoke(main, ["registry", "remove-source", "https://example.test/index.json"])
    assert r.exit_code == 0
