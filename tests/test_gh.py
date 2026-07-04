"""ROADMAP E1.3 — native PR feedback: sticky comment + Check Run.

Exercises loophole/gh.py against a real local HTTP server standing in for
the GitHub API (same technique as tests/test_feedback.py's chuzom mock),
plus the CLI's pre-flight/secret-scrub guards.
"""
from __future__ import annotations

import http.server
import json
import os
import subprocess
import tempfile
import threading

import pytest
from click.testing import CliRunner

from loophole import gh
from loophole.cli import main
from loophole.provider import Completion, Provider


# ---- pr_context() ------------------------------------------------------------

def test_pr_context_none_outside_actions(monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    assert gh.pr_context() is None


def test_pr_context_parses_pull_request_event(monkeypatch, tmp_path):
    event = {"pull_request": {"number": 42, "head": {"sha": "abc123"}}}
    p = tmp_path / "event.json"
    p.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_REPOSITORY", "Chuzom/loophole")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(p))
    ctx = gh.pr_context()
    assert ctx == {"repo": "Chuzom/loophole", "pr_number": 42, "sha": "abc123"}


def test_pr_context_none_on_non_pr_event(monkeypatch, tmp_path):
    p = tmp_path / "event.json"
    p.write_text(json.dumps({"ref": "refs/heads/main"}))   # a push event, no PR
    monkeypatch.setenv("GITHUB_REPOSITORY", "Chuzom/loophole")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(p))
    assert gh.pr_context() is None


# ---- annotations_from_boundary_events() --------------------------------------

def test_annotations_extract_clean_paths_only():
    events = [
        {"kind": "write_glob_violation",
         "payload": {"violations": [
             "tests/test_auth.py is a protected path",
             "src/app.py is outside allowed writes ['lib/**']",
             "/etc/passwd is a protected path",              # absolute -> skipped
             "../secrets.env is a protected path",            # traversal -> skipped
         ]}},
        {"kind": "merge_gate_reject", "payload": {"failures": ["x"]}},   # wrong kind
    ]
    ann = gh.annotations_from_boundary_events(events)
    paths = [a["path"] for a in ann]
    assert paths == ["tests/test_auth.py", "src/app.py"]
    assert all(a["annotation_level"] == "failure" for a in ann)


# ---- find_leaked_secrets() ----------------------------------------------------

def test_find_leaked_secrets_catches_real_leak():
    env = {"ANTHROPIC_API_KEY": "sk-supersecretvalue123456"}
    clean = "everything looks fine, no leak here"
    dirty = "oops the key is sk-supersecretvalue123456 right there"
    assert gh.find_leaked_secrets(clean, env=env) == []
    assert gh.find_leaked_secrets(dirty, env=env) == ["ANTHROPIC_API_KEY"]


def test_find_leaked_secrets_no_false_positive_on_normal_prose():
    # report prose routinely SAYS "ANTHROPIC_API_KEY" without leaking its value
    env = {"ANTHROPIC_API_KEY": "sk-supersecretvalue123456"}
    text = ("Secrets never reach verifiers — your ANTHROPIC_API_KEY and friends "
           "are scrubbed from the subprocess environment.")
    assert gh.find_leaked_secrets(text, env=env) == []


def test_find_leaked_secrets_ignores_short_values():
    env = {"SOME_TOKEN": "ab"}         # too short to be a meaningful secret
    assert gh.find_leaked_secrets("ab is a short string", env=env) == []


# ---- redact_leaked_secrets() ---------------------------------------------------

def test_redact_leaked_secrets_replaces_the_value():
    env = {"ANTHROPIC_API_KEY": "sk-supersecretvalue123456"}
    dirty = "oops the key is sk-supersecretvalue123456 right there"
    redacted, leaked = gh.redact_leaked_secrets(dirty, env=env)
    assert leaked == ["ANTHROPIC_API_KEY"]
    assert "sk-supersecretvalue123456" not in redacted
    assert "[REDACTED:ANTHROPIC_API_KEY]" in redacted


def test_redact_leaked_secrets_passes_clean_text_through_unchanged():
    env = {"ANTHROPIC_API_KEY": "sk-supersecretvalue123456"}
    clean = "everything looks fine, no leak here"
    redacted, leaked = gh.redact_leaked_secrets(clean, env=env)
    assert leaked == []
    assert redacted == clean


# ---- render_comment_body() ----------------------------------------------------

def test_render_comment_body_shape():
    result = {"status": "done", "verified_done": True, "goal": "build a thing",
             "rounds": 2, "verifier_rejections": 1, "cheats_blocked": 0,
             "residual_risk_text": "REPORT TEXT HERE"}
    body = gh.render_comment_body(result)
    assert "✅" in body and "DONE" in body
    assert "build a thing" in body
    assert "REPORT TEXT HERE" in body
    assert "<details>" in body


# ---- upsert_pr_comment() / create_check_run() against a fake GitHub API -------

class _FakeGitHub(http.server.BaseHTTPRequestHandler):
    comments = [{"id": 999, "body": "unrelated comment"}]
    posted = []
    patched = []
    check_runs = []

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if "/comments" in self.path:
            return self._send(200, self.comments)
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        if "/check-runs" in self.path:
            self.check_runs.append(payload)
            return self._send(201, {"id": 1, **payload})
        self.posted.append(payload)
        return self._send(201, {"id": 1000, **payload})

    def do_PATCH(self):
        n = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        self.patched.append(payload)
        return self._send(200, {"id": 999, **payload})

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_github(monkeypatch):
    _FakeGitHub.comments = [{"id": 999, "body": "unrelated comment"}]
    _FakeGitHub.posted = []
    _FakeGitHub.patched = []
    _FakeGitHub.check_runs = []
    srv = http.server.HTTPServer(("127.0.0.1", 0), _FakeGitHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(gh, "API", "http://127.0.0.1:{}".format(srv.server_address[1]))
    yield _FakeGitHub
    srv.shutdown()


def test_upsert_pr_comment_creates_when_no_existing(fake_github):
    gh.upsert_pr_comment("Chuzom/loophole", 7, "tok", "hello world")
    assert len(fake_github.posted) == 1
    assert gh._MARKER in fake_github.posted[0]["body"]
    assert fake_github.patched == []


def test_upsert_pr_comment_edits_existing_marked_comment(fake_github):
    fake_github.comments = [
        {"id": 999, "body": "unrelated"},
        {"id": 1001, "body": gh._MARKER + "\nold report"},
    ]
    gh.upsert_pr_comment("Chuzom/loophole", 7, "tok", "new report")
    assert fake_github.posted == []
    assert len(fake_github.patched) == 1
    assert "new report" in fake_github.patched[0]["body"]


def test_create_check_run_sends_conclusion_and_annotations(fake_github):
    gh.create_check_run("Chuzom/loophole", "deadbeef", "tok", "loophole / acceptance",
                        "failure", "FAILED", "summary text",
                        annotations=[{"path": "x.py", "start_line": 1, "end_line": 1,
                                     "annotation_level": "failure", "message": "m"}])
    assert len(fake_github.check_runs) == 1
    cr = fake_github.check_runs[0]
    assert cr["conclusion"] == "failure"
    assert cr["status"] == "completed"
    assert cr["output"]["annotations"][0]["path"] == "x.py"


# ---- CLI pre-flight for --comment ---------------------------------------------

def test_comment_without_token_exits_2(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    r = CliRunner().invoke(main, ["run", "hello", "--verify", "true", "--comment"])
    assert r.exit_code == 2, r.output
    assert "GITHUB_TOKEN" in r.output


def test_comment_outside_pr_context_exits_2(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    r = CliRunner().invoke(main, ["run", "hello", "--verify", "true", "--comment"])
    assert r.exit_code == 2, r.output
    assert "pull_request" in r.output


# ---- end-to-end: a real run posts to the fake GitHub API ----------------------

class _FakePlanner(Provider):
    name = "fake"

    def __init__(self, tasks):
        self._text = json.dumps(tasks)

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        return Completion(text=self._text)


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_gh_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def test_run_comment_end_to_end_posts_comment_and_check_run(
        fake_github, monkeypatch, tmp_path):
    tasks = [{"id": "t1", "description": "create add.py with add(a,b)",
             "depends_on": [], "reads": [], "writes": ["add.py"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    event = {"pull_request": {"number": 11, "head": {"sha": "cafebabe"}}}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Chuzom/loophole")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    write_add = "python3 -c \"open('add.py','w').write('def add(a,b): return a+b')\""
    verify = 'python3 -c "from add import add; assert add(2,3)==5; print(\'ok\')"'
    r = CliRunner().invoke(main, [
        "run", "implement add(a,b) in add.py returning a+b",
        "--verify", verify, "--workspace", ws,
        "--executor-command", write_add, "--skip-critique", "--comment",
        "--no-watch", "--max-rounds", "2", "--db", db,
    ])
    assert r.exit_code == 0, r.output
    assert len(fake_github.posted) == 1                 # the PR comment
    assert "✅" in fake_github.posted[0]["body"]
    assert len(fake_github.check_runs) == 1
    assert fake_github.check_runs[0]["conclusion"] == "success"


# ---- regression: a secret-shaped leak never reaches JSON/comment/check-run ----
#
# ROADMAP_EXECUTION.md step 3. The most realistic leak vector isn't a verifier's
# raw stdout (report.to_json() only ever exposes a filtered `failures` subset,
# never raw output) — it's a user goal string that happens to embed a real
# secret value by mistake. That flows straight into result["goal"] and
# result["residual_risk_text"], so it exercises the actual JSON schema, not a
# contrived path.

_FAKE_SECRET = "sk-should-never-leak-9f8e7d6c5b4a"


def test_run_json_file_redacts_a_secret_leaked_via_the_goal_text(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_DEMO_API_KEY", _FAKE_SECRET)
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["x.txt"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    json_path = os.path.join(str(tmp_path), "result.json")
    r = CliRunner().invoke(main, [
        "run", "fix the outage related to " + _FAKE_SECRET,
        "--verify", "true", "--workspace", ws,
        "--executor-command", "python3 -c \"open('x.txt','w').write('x')\"",
        "--skip-critique", "--no-watch", "--max-rounds", "2",
        "--db", db, "--json-file", json_path,
    ])
    assert r.exit_code == 0, r.output
    assert "WARNING" in r.output and "FAKE_DEMO_API_KEY" in r.output
    raw = open(json_path).read()
    assert _FAKE_SECRET not in raw
    assert "[REDACTED:FAKE_DEMO_API_KEY]" in raw
    data = json.loads(raw)
    assert _FAKE_SECRET not in data["goal"]


def test_run_comment_redacts_a_secret_leaked_via_the_goal_text(
        fake_github, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_DEMO_API_KEY", _FAKE_SECRET)
    tasks = [{"id": "t1", "description": "noop", "depends_on": [], "reads": [], "writes": ["x.txt"]}]
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _FakePlanner(tasks))
    event = {"pull_request": {"number": 12, "head": {"sha": "deadbeef"}}}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Chuzom/loophole")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))

    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    r = CliRunner().invoke(main, [
        "run", "fix the outage related to " + _FAKE_SECRET,
        "--verify", "true", "--workspace", ws,
        "--executor-command", "python3 -c \"open('x.txt','w').write('x')\"",
        "--skip-critique", "--comment", "--no-watch", "--max-rounds", "2", "--db", db,
    ])
    assert r.exit_code == 0, r.output
    # posted anyway (redacted upstream, so _post_pr_feedback's own belt-and-
    # suspenders check now finds nothing left to refuse on)
    assert len(fake_github.posted) == 1
    assert _FAKE_SECRET not in fake_github.posted[0]["body"]
    assert "[REDACTED:FAKE_DEMO_API_KEY]" in fake_github.posted[0]["body"]
    assert len(fake_github.check_runs) == 1
    assert _FAKE_SECRET not in json.dumps(fake_github.check_runs[0])
