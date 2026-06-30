"""Verification — the only thing that can say "done".

Verifiers are adapters that emit METRICS, not just a boolean (council critique D:
progress must be measurable). The pytest adapter parses counts so the loop can
tell whether a non-passing round still made progress.

The Verification Boundary (council critique B) defends the verifier from the
agent: protected paths must be unchanged vs the base, and the test count may not
silently drop. Verification runs from a fresh checkout of the merged candidate,
never the agent's dirty worktree (the caller is responsible for providing that
clean directory).
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Dict, List, Optional

from .contract import GoalContract, Verifier, VerifierKind

if TYPE_CHECKING:
    from .provider import Provider


@dataclass
class VerifierResult:
    name: str
    passed: bool
    metrics: Dict[str, float] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    output: str = ""

    def signature(self) -> str:
        """A normalized failure signature for stuckness detection."""
        if self.passed:
            return ""
        sig = "|".join(sorted(self.failures))
        return re.sub(r"0x[0-9a-f]+|line \d+|:\d+:", "", sig)[:500]


@dataclass
class VerifyVerdict:
    passed: bool                       # goal contract satisfied
    results: List[VerifierResult] = field(default_factory=list)
    boundary_violations: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    @property
    def metric_score(self) -> float:
        """Aggregate 'distance to done' — higher is better. Used for progress."""
        score = 0.0
        for r in self.results:
            if r.passed:
                score += 1000.0
            score -= r.metrics.get("failed", 0)
            score -= r.metrics.get("errors", 0)
            score += r.metrics.get("passed", 0)
        if self.boundary_violations:
            score -= 10000.0
        return score

    def signature(self) -> str:
        parts = [r.signature() for r in self.results if not r.passed]
        return "|".join(p for p in parts if p) + "||" + "|".join(sorted(self.boundary_violations))


def _count(label: str, line: str) -> float:
    m = re.search(r"(\d+)\s+" + label, line)
    return float(m.group(1)) if m else 0.0


def parse_pytest(output: str) -> Dict[str, float]:
    metrics = {"passed": 0.0, "failed": 0.0, "errors": 0.0, "skipped": 0.0}
    # Use the last line that mentions a pytest outcome keyword as the summary.
    for line in reversed(output.strip().splitlines()):
        if re.search(r"\d+\s+(passed|failed|error|skipped)", line):
            metrics["passed"] = _count("passed", line)
            metrics["failed"] = _count("failed", line)
            metrics["errors"] = _count("errors?", line)
            metrics["skipped"] = _count("skipped", line)
            break
    metrics["total"] = metrics["passed"] + metrics["failed"] + metrics["errors"] + metrics["skipped"]
    return metrics


_SECRET_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|API)", re.I)


def _scrub_env(extra: Dict[str, str]) -> Dict[str, str]:
    """S2 fix: never hand provider API keys / secrets to verifier subprocesses.

    Verifiers run agent-influenced code (pytest imports the candidate). Passing the
    full environment lets any dependency exfiltrate ANTHROPIC_API_KEY, cloud creds,
    etc. We drop anything that looks like a secret and keep the rest (PATH, HOME…).
    """
    safe = {k: val for k, val in os.environ.items() if not _SECRET_RE.search(k)}
    safe.update(extra or {})
    return safe


def run_command_verifier(v: Verifier, cwd: str, timeout: int = 600) -> VerifierResult:
    name = "hard:" + (v.command or "")
    try:
        proc = subprocess.run(v.command, shell=True, cwd=cwd, timeout=timeout,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, env=_scrub_env(v.environment))
        output = proc.stdout or ""
        rc = proc.returncode
    except subprocess.TimeoutExpired as e:
        return VerifierResult(name=name, passed=False,
                              failures=["timeout"], output=str(e.output or ""))
    metrics: Dict[str, float] = {}
    cmd = (v.command or "")
    if "pytest" in cmd or "py.test" in cmd:
        metrics = parse_pytest(output)
    failures: List[str] = []
    if rc != 0:
        # grab failing test ids if present
        failures = re.findall(r"FAILED (\S+)", output) or \
                   re.findall(r"^E\s+(.*)$", output, re.M)[:5] or ["exit {}".format(rc)]
    return VerifierResult(name=name, passed=(rc == 0), metrics=metrics,
                          failures=failures, output=output[-4000:])


_SOFT_SYSTEM = """You are a strict acceptance reviewer. You are given a rubric and
a snapshot of a candidate's files. Decide whether the candidate SATISFIES the rubric.
You can only VETO (reject); a hard automated check has already passed.
Default to NO veto unless the rubric is clearly violated.
Return ONLY JSON: {"satisfied": true|false, "reasons": ["..."]}."""


def _snapshot(cand_dir: str, max_files: int = 25, max_bytes: int = 20000) -> str:
    """A compact text snapshot of the candidate workspace for the soft judge."""
    parts: List[str] = []
    budget = max_bytes
    for dirpath, dirs, files in os.walk(cand_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".loophole_worktrees")]
        for f in sorted(files):
            if len(parts) >= max_files or budget <= 0:
                break
            rel = os.path.relpath(os.path.join(dirpath, f), cand_dir)
            content = _read(os.path.join(dirpath, f)) or b""
            try:
                text = content.decode("utf-8")
            except UnicodeDecodeError:
                continue
            chunk = text[: min(2000, budget)]
            budget -= len(chunk)
            parts.append("--- {} ---\n{}".format(rel, chunk))
    return "\n\n".join(parts) or "(no readable files)"


def evaluate_soft_verifier(v: Verifier, cand_dir: str,
                           judge: "Optional[Provider]") -> VerifierResult:
    """Evaluate a soft (rubric) verifier via an LLM. May only veto (GAP 2 fix).

    With no judge available, abstains (passed=True) and says so — a soft verifier
    must never block on infrastructure absence.
    """
    name = "soft:" + (v.rubric or "")[:48]
    if judge is None:
        return VerifierResult(name=name, passed=True,
                              output="no soft judge configured; abstaining")
    from .provider import Msg  # local import to avoid cycle
    user = "RUBRIC:\n{}\n\nCANDIDATE FILES:\n{}".format(v.rubric, _snapshot(cand_dir))
    try:
        comp = judge.complete([Msg("system", _SOFT_SYSTEM), Msg("user", user)],
                              temperature=0.1)
    except Exception as e:  # never let a soft check crash the run
        return VerifierResult(name=name, passed=True,
                              output="soft judge error, abstaining: {}".format(e))
    d = _parse_json_obj(comp.text)
    satisfied = bool(d.get("satisfied", True))   # fail-open: abstain on parse miss
    reasons = [str(x) for x in d.get("reasons", [])]
    return VerifierResult(name=name, passed=satisfied,
                          failures=([] if satisfied else (reasons or ["soft rubric not satisfied"])),
                          output=comp.text[:2000])


def _parse_json_obj(text: str) -> dict:
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1:
        return {}
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return {}


def check_boundary(contract: GoalContract, base_dir: str, candidate_dir: str) -> List[str]:
    """Enforce the Verification Boundary. Returns a list of violations.

    base_dir:      clean checkout of the goal's starting commit
    candidate_dir: clean checkout of the merged candidate to be verified
    """
    violations: List[str] = []
    for pattern in contract.all_protected_paths:
        for rel in _glob_files(candidate_dir, pattern) | _glob_files(base_dir, pattern):
            a = os.path.join(base_dir, rel)
            b = os.path.join(candidate_dir, rel)
            if _read(a) != _read(b):
                violations.append("protected path modified: {}".format(rel))
    return violations


def check_test_count(contract: GoalContract, candidate_metrics: Dict[str, float],
                     baseline_total: Optional[float]) -> List[str]:
    """Detect the agent weakening the suite by deleting/skipping tests."""
    violations: List[str] = []
    if baseline_total is None:
        return violations
    for v in contract.hard_verifiers:
        if v.expected_test_delta is None:
            continue
        floor = baseline_total + v.expected_test_delta
        total = candidate_metrics.get("total", 0)
        if total < floor:
            violations.append(
                "test count dropped: {} < expected floor {} (baseline {} + delta {})"
                .format(int(total), int(floor), int(baseline_total), v.expected_test_delta))
    return violations


def _glob_files(root: str, pattern: str):
    found = set()
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            rel = os.path.relpath(os.path.join(dirpath, f), root)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(f, pattern):
                found.add(rel)
    return found


def _read(path: str) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None
