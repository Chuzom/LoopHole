"""ROADMAP E3.3 — `loophole run --verify-http`: the health-check verifier
wired into the CLI (http_check_command itself is unit-tested in
test_contract.py). Real sandbox, real network, real HTTP server, real merge
gate/verifier boundary — only the planner is faked (no LLM needed).
"""
from __future__ import annotations

import functools
import http.server
import json
import os
import subprocess
import tempfile
import threading

from click.testing import CliRunner

from loophole.cli import main
from loophole.provider import Completion, Provider


class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_verify_http_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def _health_server(tmp_path):
    """A real local HTTP server serving tmp_path/health/status — explicit
    `directory=` so it's immune to the process cwd changing later (a real
    footgun with SimpleHTTPRequestHandler's default cwd-at-request-time
    behavior)."""
    srv_dir = os.path.join(str(tmp_path), "health_srv")
    os.makedirs(os.path.join(srv_dir, "health"))
    with open(os.path.join(srv_dir, "health", "status"), "w") as f:
        f.write("ok")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=srv_dir)
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _run(monkeypatch, tmp_path, extra_args):
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["x.txt"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    runner = CliRunner()
    return runner.invoke(main, [
        "run", "verify-http test",
        "--executor-command", "python3 -c \"open('x.txt','w').write('x')\"",
        "--skip-critique", "--no-watch", "--max-rounds", "2",
        "--workspace", ws, "--db", db, "--json", *extra_args,
    ])


def test_verify_http_passes_on_matching_status(monkeypatch, tmp_path):
    srv, port = _health_server(tmp_path)
    try:
        r = _run(monkeypatch, tmp_path, [
            "--verify-http", "http://127.0.0.1:{}/health/status".format(port),
            "--verify-http-timeout", "2", "--verify-http-retries", "2",
        ])
        assert r.exit_code == 0, r.output
        data = json.loads(r.output[r.output.rindex("\n{\n"):])
        assert data["verified_done"] is True
        assert any(v["passed"] for v in data["verified"])
    finally:
        srv.shutdown()


def test_verify_http_composes_with_verify(monkeypatch, tmp_path):
    """--verify and --verify-http are additive — BOTH must pass."""
    srv, port = _health_server(tmp_path)
    try:
        r = _run(monkeypatch, tmp_path, [
            "--verify", "true",
            "--verify-http", "http://127.0.0.1:{}/health/status".format(port),
            "--verify-http-timeout", "2", "--verify-http-retries", "2",
        ])
        assert r.exit_code == 0, r.output
        data = json.loads(r.output[r.output.rindex("\n{\n"):])
        assert len(data["verified"]) == 2
        assert all(v["passed"] for v in data["verified"])
    finally:
        srv.shutdown()


def test_verify_http_fails_on_wrong_status(monkeypatch, tmp_path):
    srv, port = _health_server(tmp_path)
    try:
        r = _run(monkeypatch, tmp_path, [
            "--verify-http", "http://127.0.0.1:{}/health/status".format(port),
            "--verify-http-status", "404",   # real response is 200
            "--verify-http-timeout", "1", "--verify-http-retries", "1",
        ])
        assert r.exit_code == 1, r.output
        data = json.loads(r.output[r.output.rindex("\n{\n"):])
        assert data["verified_done"] is False
    finally:
        srv.shutdown()


def test_verify_http_alone_skips_the_human_checkpoint(monkeypatch, tmp_path):
    """--verify-http alone (no --verify) must not ALSO get a spurious human
    checkpoint added — it's a real, grantable hard verifier on its own."""
    srv, port = _health_server(tmp_path)
    try:
        r = _run(monkeypatch, tmp_path, [
            "--verify-http", "http://127.0.0.1:{}/health/status".format(port),
            "--verify-http-timeout", "2", "--verify-http-retries", "2",
        ])
        assert r.exit_code == 0, r.output
        assert "human checkpoint" not in r.output.lower()
    finally:
        srv.shutdown()


def test_verify_http_invalid_status_is_a_usage_error(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path, [
        "--verify-http", "http://127.0.0.1:1/x", "--verify-http-status", "999",
    ])
    assert r.exit_code == 2, r.output
    assert "--verify-http" in r.output
