"""STRAT-1 — infer a starter Goal Contract from a repository.

`loophole init` lowers the biggest adoption barrier: authoring a contract from
scratch. We inspect the filesystem ONLY (no code execution, no model calls) and
emit a `loophole.json` the user edits. We infer what we safely can — the test
runner becomes a hard verifier, plus protected paths, a narrowed write-allowlist,
and an anti-reward-hacking test-delta. The one thing we never guess is the GOAL:
it stays an explicit TODO the human fills in.
"""

from __future__ import annotations

import json
import os
import re
from typing import List, Optional, Tuple

from .contract import GoalContract, Verifier, VerifierKind

GOAL_TODO = "TODO: describe the goal in one sentence (what 'done' means)"
CONTRACT_FILENAME = "loophole.json"

_HELP = ("Edit 'goal'. The verifiers, protected_paths and allowed_writes below were "
         "INFERRED from your repo — adjust them. Then run: "
         "loophole run --contract loophole.json")


def _exists(repo: str, *parts: str) -> bool:
    return os.path.exists(os.path.join(repo, *parts))


def _read(repo: str, *parts: str) -> str:
    try:
        with open(os.path.join(repo, *parts), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _walk_has(repo: str, predicate, skip=(".git", "node_modules", "__pycache__",
                                          ".venv", "venv", ".loophole")) -> bool:
    for dirpath, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            if predicate(os.path.relpath(os.path.join(dirpath, f), repo)):
                return True
    return False


def detect_test_command(repo: str, notes: List[str]) -> Optional[str]:
    """Best-effort test-runner detection -> a hard verifier command, or None."""
    pyproject = _read(repo, "pyproject.toml")
    has_pytest_tests = _walk_has(
        repo, lambda rel: os.path.basename(rel).startswith("test_")
        and rel.endswith(".py"))
    if ("[tool.pytest" in pyproject or _exists(repo, "pytest.ini")
            or _exists(repo, "conftest.py") or has_pytest_tests):
        notes.append("detected pytest -> hard verifier 'pytest -q'")
        return "pytest -q"
    if has_pytest_tests:  # python tests but no pytest signal
        notes.append("detected python tests -> hard verifier 'python -m unittest'")
        return "python -m unittest"
    pkg = _read(repo, "package.json")
    if pkg:
        try:
            if "test" in (json.loads(pkg).get("scripts") or {}):
                notes.append("detected package.json test script -> 'npm test'")
                return "npm test"
        except json.JSONDecodeError:
            pass
    if _walk_has(repo, lambda rel: rel.endswith("_test.go")):
        notes.append("detected Go tests -> 'go test ./...'")
        return "go test ./..."
    if _exists(repo, "Cargo.toml"):
        notes.append("detected Cargo -> 'cargo test'")
        return "cargo test"
    mk = _read(repo, "Makefile")
    if re.search(r"^test:", mk, re.M):
        notes.append("detected Makefile test target -> 'make test'")
        return "make test"
    return None


_PROTECT_CANDIDATES = [
    (".github/workflows", ".github/workflows/**"),
    ("poetry.lock", "poetry.lock"),
    ("package-lock.json", "package-lock.json"),
    ("yarn.lock", "yarn.lock"),
    ("Cargo.lock", "Cargo.lock"),
    ("go.sum", "go.sum"),
    ("LICENSE", "LICENSE"),
]


def detect_protected(repo: str) -> List[str]:
    """Conservative protected-path set: CI config, lockfiles, license, the contract
    file itself. (tests/** is offered as a note, not enforced — new tests are
    usually wanted; weakening is caught by the test-delta floor.)"""
    protected = [glob for path, glob in _PROTECT_CANDIDATES if _exists(repo, path)]
    protected.append(CONTRACT_FILENAME)   # never let an agent rewrite its own contract
    return protected


def detect_allowed_writes(repo: str, notes: List[str]) -> List[str]:
    """Narrow the write-allowlist to obvious source/test dirs; fall back to ['**']."""
    allowed: List[str] = []
    if _exists(repo, "src") and os.path.isdir(os.path.join(repo, "src")):
        allowed.append("src/**")
    # a top-level python package (dir with __init__.py)
    for name in sorted(os.listdir(repo)) if os.path.isdir(repo) else []:
        p = os.path.join(repo, name)
        if os.path.isdir(p) and name not in (".git", ".venv", "venv", "tests") \
                and _exists(p, "__init__.py"):
            allowed.append(name + "/**")
    if _exists(repo, "tests") and os.path.isdir(os.path.join(repo, "tests")):
        allowed.append("tests/**")
    if not allowed:
        notes.append("could not infer source layout -> allowed_writes defaults to "
                     "['**'] (narrow it manually for tighter control)")
        return ["**"]
    notes.append("inferred allowed_writes: " + ", ".join(allowed))
    return allowed


def detect_contract(repo: str) -> Tuple[GoalContract, List[str]]:
    """Inspect ``repo`` and return a starter contract plus human-readable notes."""
    notes: List[str] = []
    verifiers: List[Verifier] = []
    cmd = detect_test_command(repo, notes)
    if cmd:
        verifiers.append(Verifier(kind=VerifierKind.HARD, command=cmd,
                                  expected_test_delta=0))
    else:
        notes.append("no test runner detected -> added a HUMAN checkpoint; "
                     "replace it with a real --verify command for autonomy")
        verifiers.append(Verifier(kind=VerifierKind.HUMAN,
                                  prompt="Does the result satisfy the goal?"))
    contract = GoalContract(
        goal=GOAL_TODO,
        verifiers=verifiers,
        protected_paths=detect_protected(repo),
        allowed_writes=detect_allowed_writes(repo, notes),
    )
    return contract, notes


def write_starter(contract: GoalContract, path: str) -> None:
    """Write the contract as JSON with a leading _help key (JSON has no comments).

    Empty/default fields are omitted so the scaffold reads as the few things a
    human actually edits, not a wall of ``null``s. ``from_dict`` refills every
    default on load, so the compacted file is equivalent."""
    data = json.loads(contract.to_json())
    data["verifiers"] = [_compact_verifier(v) for v in data.get("verifiers", [])]
    for k in ("acceptance_criteria", "non_functional"):
        if not data.get(k):
            data.pop(k, None)
    out = {"_help": _HELP}
    out.update(data)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(out, indent=2) + "\n")


# fields dropped from a scaffolded verifier when they hold their default/empty value
_VERIFIER_DEFAULTS = {
    "command": None, "rubric": None, "prompt": None, "trusted_inputs": [],
    "protected_paths": [], "expected_test_delta": None, "environment": {},
    "allow_network": False, "params": {},
}


def _compact_verifier(v: dict) -> dict:
    return {k: val for k, val in v.items()
            if k not in _VERIFIER_DEFAULTS or val != _VERIFIER_DEFAULTS[k]}


def load_contract(path: str) -> GoalContract:
    """Load a contract from a local path OR an http(s)/file URL (acceptance-spec-
    as-code: contracts can be shared). Ignores the _help key."""
    if path.startswith(("http://", "https://", "file://")):
        import urllib.request
        with urllib.request.urlopen(path, timeout=15) as r:   # nosec - operator-supplied
            raw = r.read().decode("utf-8")
    else:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    return GoalContract.from_json(raw)


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")


def list_templates() -> List[str]:
    """Names of the bundled starter contracts (without the .json extension)."""
    try:
        return sorted(f[:-5] for f in os.listdir(TEMPLATES_DIR) if f.endswith(".json"))
    except OSError:
        return []


def load_template_raw(name: str) -> str:
    """Raw JSON of a bundled template, or raise ValueError listing the choices."""
    path = os.path.join(TEMPLATES_DIR, name + ".json")
    if not os.path.exists(path):
        raise ValueError("unknown template '{}'. Available: {}".format(
            name, ", ".join(list_templates()) or "(none)"))
    # parse-check it (also validates it's a usable contract shape)
    raw = _read_file(path)
    GoalContract.from_json(raw)
    return raw


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
