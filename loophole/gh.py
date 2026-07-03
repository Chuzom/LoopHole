"""GitHub REST integration — sticky PR comments + Check Runs (ROADMAP E1.3).

The visible signal that makes loophole feel like CI: a PR gets a single,
self-updating comment with the Residual-Risk Report, plus a Check Run whose
conclusion mirrors the verifier's verdict (annotated with the exact files an
agent tried to cheat through). Uses stdlib ``urllib`` — same pattern as
``provider.py``'s HTTP paths — so this stays a soft dependency most installs
never touch.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

API = "https://api.github.com"

# A single sticky comment: loophole edits its OWN previous comment on re-runs
# instead of growing a new one each time. The marker is how we find it again.
_MARKER = "<!-- loophole-run-report -->"


class GhError(RuntimeError):
    """A GitHub API call failed — network, auth, or permissions."""


def _headers(token: str) -> Dict[str, str]:
    return {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, token: str, payload: Optional[dict] = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(token), method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise GhError("{} {} -> {} {}".format(
            method, url, e.code, e.read().decode(errors="replace")[:300])) from e
    except urllib.error.URLError as e:
        raise GhError("{} {} -> {}".format(method, url, e)) from e


def pr_context() -> Optional[Dict[str, Any]]:
    """Extract ``{repo, pr_number, sha}`` from the GitHub Actions environment
    when running inside a pull_request-triggered job. None otherwise — the
    caller decides whether that's a hard error (e.g. --comment demands it)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not repo or not event_path or not os.path.exists(event_path):
        return None
    try:
        with open(event_path, encoding="utf-8") as f:
            event = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    pr = event.get("pull_request") or {}
    number = pr.get("number")
    if not number:
        return None
    sha = (pr.get("head") or {}).get("sha") or os.environ.get("GITHUB_SHA")
    return {"repo": repo, "pr_number": number, "sha": sha}


def upsert_pr_comment(repo: str, pr_number: int, token: str, body: str) -> dict:
    """Post the report as a PR comment, editing loophole's own previous
    comment in place on re-runs rather than growing a new one each time."""
    marked = _MARKER + "\n" + body
    comments = _request("GET", "{}/repos/{}/issues/{}/comments?per_page=100".format(
        API, repo, pr_number), token)
    existing = next((c for c in comments if _MARKER in (c.get("body") or "")), None)
    if existing:
        return _request("PATCH", "{}/repos/{}/issues/comments/{}".format(
            API, repo, existing["id"]), token, {"body": marked})
    return _request("POST", "{}/repos/{}/issues/{}/comments".format(
        API, repo, pr_number), token, {"body": marked})


def annotations_from_boundary_events(boundary_events: List[dict]) -> List[dict]:
    """Turn write-allowlist violations into Check-Run annotations pointing at
    the offending file. loophole doesn't track exact line numbers for a
    boundary violation, so this is a whole-file marker (line 1) — still far
    more useful than a summary line buried in a log."""
    out: List[dict] = []
    for e in boundary_events:
        if e.get("kind") != "write_glob_violation":
            continue
        for v in (e.get("payload") or {}).get("violations", []):
            path = v.split(" is ", 1)[0].strip()
            if not path or path.startswith("/") or ".." in path.split(os.sep):
                continue   # only annotate a clean repo-relative path
            out.append({
                "path": path, "start_line": 1, "end_line": 1,
                "annotation_level": "failure",
                "title": "loophole: write-allowlist violation",
                "message": v,
            })
    return out


def create_check_run(repo: str, sha: str, token: str, name: str, conclusion: str,
                     title: str, summary: str,
                     annotations: Optional[List[dict]] = None) -> dict:
    """A single completed Check Run (loophole's run is synchronous, so there's
    no in_progress phase worth reporting separately)."""
    output: Dict[str, Any] = {"title": title, "summary": summary}
    if annotations:
        output["annotations"] = annotations[:50]   # GitHub's own per-call cap
    payload = {"name": name, "head_sha": sha, "status": "completed",
              "conclusion": conclusion, "output": output}
    return _request("POST", "{}/repos/{}/check-runs".format(API, repo), token, payload)


def find_leaked_secrets(text: str, env: Optional[Dict[str, str]] = None) -> List[str]:
    """Defense in depth before posting anything to GitHub: return the names of
    env vars whose secret-shaped VALUE appears verbatim in `text`.

    Verifiers already run with secrets scrubbed from their environment
    (sandbox.scrub_env), so this should always come back empty — a non-empty
    result means something upstream leaked and the caller must refuse to post.
    """
    from .sandbox import _SECRET_RE
    env = os.environ if env is None else env
    leaked = []
    for k, v in env.items():
        if v and len(v) >= 6 and _SECRET_RE.search(k) and v in text:
            leaked.append(k)
    return leaked


def render_comment_body(result: Dict[str, Any]) -> str:
    """The sticky PR comment: a compact header + the Residual-Risk Report."""
    emoji = "✅" if result.get("verified_done") else "❌"
    lines = [
        "## {} loophole — {}".format(emoji, result.get("status", "unknown").upper()),
        "",
        "**Goal:** {}".format(result.get("goal", "")),
        "**Rounds:** {}  ·  **Verifier rejections:** {}  ·  **Cheats blocked:** {}".format(
            result.get("rounds", 0), result.get("verifier_rejections", 0),
            result.get("cheats_blocked", 0)),
        "",
        "<details><summary>Residual-Risk Report</summary>",
        "",
        "```text",
        result.get("residual_risk_text", ""),
        "```",
        "</details>",
    ]
    return "\n".join(lines)
