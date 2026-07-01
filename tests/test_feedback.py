"""Routing-quality feedback: verifier verdict -> Chuzom (Phase 1a)."""

import json
import os

from loophole.feedback import build_record, emit, _provider_label
from loophole.state import Store


class _Fake:
    name = "ollama"
    model = "qwen3-coder:30b"


class _Budget:
    spent_tokens = 4200
    spent_usd = 0.0


def test_provider_label_and_unwrap():
    assert _provider_label(_Fake()) == "ollama:qwen3-coder:30b"

    class _Wrap:
        _inner = _Fake()
    assert _provider_label(_Wrap()) == "ollama:qwen3-coder:30b"


def test_build_record_marks_verified_done(tmp_path):
    store = Store(os.path.join(str(tmp_path), "s.db"))
    gid = store.create_goal("{}", str(tmp_path))
    rec = build_record(store, gid, "done", 2, _Budget(),
                       planner_label="chuzom:moderate",
                       executor_label="ollama:qwen3-coder:30b", ts=1.0)
    assert rec["verified_done"] is True
    assert rec["executor_model"] == "ollama:qwen3-coder:30b"
    assert rec["tokens"] == 4200
    assert rec["source"] == "loophole"

    rec2 = build_record(store, gid, "paused", 3, _Budget(),
                        planner_label="p", executor_label="e", ts=2.0)
    assert rec2["verified_done"] is False


def test_emit_falls_back_to_jsonl(tmp_path, monkeypatch):
    monkeypatch.delenv("CHUZOM_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    rec = {"source": "loophole", "verified_done": True, "executor_model": "x"}
    assert emit(rec) == "file"
    path = os.path.join(str(tmp_path), ".chuzom", "quality_feedback.jsonl")
    assert json.loads(open(path).read().strip())["executor_model"] == "x"


def test_emit_prefers_http_when_reachable(tmp_path, monkeypatch):
    import http.server
    import threading

    posted = {}

    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            posted["path"] = self.path
            n = int(self.headers.get("Content-Length") or 0)
            posted["body"] = json.loads(self.rfile.read(n) or b"{}")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:{}".format(srv.server_address[1])
        assert emit({"source": "loophole", "ok": 1}, chuzom_url=url) == "http"
        assert posted["path"] == "/feedback"
        assert posted["body"]["ok"] == 1
    finally:
        srv.shutdown()
