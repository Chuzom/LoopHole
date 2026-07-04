"""Goal Contract — the explicit, falsifiable definition of "done".

loophole refuses to run a goal that cannot be checked. The Goal Contract makes
the acceptance criteria, verifier boundary, and mutation policy explicit so that
"complete" means "the candidate satisfies THIS contract under a trusted verifier
boundary" — not "an LLM felt the goal was achieved".
"""

from __future__ import annotations

import fnmatch
import json
import os
import shlex
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, List, Optional


class VerifierKind(str, Enum):
    HARD = "hard"      # command exit-code; GRANTS completion
    SOFT = "soft"      # llm rubric; can only VETO when a hard verifier exists
    HUMAN = "human"    # checkpoint; pauses and asks


@dataclass
class Verifier:
    """A single acceptance check.

    hard:  ``command`` runs in a fresh checkout of the merged candidate; exit 0 = pass.
    soft:  ``rubric`` is given to an LLM that may only veto a hard pass.
    human: ``prompt`` is surfaced to the operator at a checkpoint.
    """
    kind: VerifierKind
    command: Optional[str] = None         # for hard
    rubric: Optional[str] = None          # for soft
    prompt: Optional[str] = None          # for human
    # Verification Boundary (fix B from the council):
    trusted_inputs: List[str] = field(default_factory=list)   # paths owned by orchestrator
    protected_paths: List[str] = field(default_factory=list)  # editing these => instant fail
    expected_test_delta: Optional[int] = None  # test count may not silently drop below baseline+delta
    environment: dict = field(default_factory=dict)
    allow_network: bool = False   # S1: opt this verifier out of the sandbox network deny
    # Module SDK: a non-builtin `kind` (a string a module registered) carries its
    # config here. Module verifiers grade at the round level — they NEVER enter the
    # deterministic per-merge gate or the verify cache (those stay hard-only).
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.kind, str):
            try:
                self.kind = VerifierKind(self.kind)   # a builtin kind
            except ValueError:
                pass                                  # a module kind — keep the string
        if self.kind == VerifierKind.HARD and not self.command:
            raise ContractError("hard verifier requires a `command`")
        if self.kind == VerifierKind.SOFT and not self.rubric:
            raise ContractError("soft verifier requires a `rubric`")
        if self.kind == VerifierKind.HUMAN and not self.prompt:
            self.prompt = "Does the result satisfy the goal? (approve/reject)"

    @property
    def kind_str(self) -> str:
        return self.kind.value if isinstance(self.kind, VerifierKind) else str(self.kind)

    @property
    def is_builtin(self) -> bool:
        return isinstance(self.kind, VerifierKind)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind_str
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Verifier":
        d = dict(d)
        raw_kind = d.get("kind", "hard")
        try:
            d["kind"] = VerifierKind(raw_kind)
        except ValueError:
            d["kind"] = raw_kind   # module kind
        return cls(**d)


def http_check_command(url: str, status: int = 200, timeout: int = 5,
                       retries: int = 3, retry_delay: int = 2) -> str:
    """Build a hard-verifier shell command: "did `url` return HTTP `status`?"
    (ROADMAP E3.3 — a first-class helper for the health-check-curl pattern
    users otherwise hand-roll every time).

    Retries a few times a few seconds apart by default — a just-started
    service returning connection-refused for the first couple of seconds is
    the common case here, not an edge case to work around by hand. Pure
    POSIX sh (no bashisms), a single line so it stores/displays cleanly as
    one Verifier.command string. The URL is shell-quoted; ``status`` is
    validated as a real int, so there's no injection surface from either.
    """
    status = int(status)
    timeout = int(timeout)
    retries = int(retries)
    retry_delay = int(retry_delay)
    if status < 100 or status > 599:
        raise ValueError("status must be a valid HTTP status code")
    if timeout < 1:
        raise ValueError("timeout must be >= 1")
    if retries < 1:
        raise ValueError("retries must be >= 1")
    if retry_delay < 0:
        raise ValueError("retry_delay must be >= 0")
    q_url = shlex.quote(url)
    parts = [
        'i=0',
        'while [ "$i" -lt {} ]'.format(retries),
        'do code="$(curl -s -o /dev/null -w \'%{{http_code}}\' --max-time {} {})"'
            .format(timeout, q_url),
        '[ "$code" = "{}" ] && exit 0'.format(int(status)),
        'i=$((i+1))',
        '[ "$i" -lt {} ] && sleep {}'.format(retries, retry_delay),
        'done',
        'exit 1',
    ]
    return "; ".join(parts)


def coverage_check_command(min_percent: int, target: str = ".",
                           pytest_args: str = "-q") -> str:
    """Build a hard-verifier command: does the suite achieve at least
    ``min_percent`` coverage of ``target``? (ROADMAP E3.3.)

    Requires ``pytest-cov`` (``pip install pytest-cov``) — loophole inspects
    your repo, it doesn't install dependencies for you; a repo without it
    will fail with pytest's own "unrecognized arguments: --cov" error, which
    is a reasonably clear signal of what's missing.

    Verified live against a real pytest-cov run: ``--cov-fail-under`` makes
    pytest's OWN exit code reflect the coverage threshold, independent of
    whether the tests themselves passed — exactly the "hard verifier, exit
    0 = done" contract this command needs to satisfy.
    """
    min_percent = int(min_percent)
    if not (0 <= min_percent <= 100):
        raise ValueError("min_percent must be between 0 and 100")
    return "pytest {} --cov={} --cov-fail-under={}".format(
        pytest_args, shlex.quote(target), min_percent)


def mutation_check_command(paths_to_mutate: str, tests_dir: Optional[str] = None) -> str:
    """Build a hard-verifier command: does mutation testing find ZERO
    surviving mutants in ``paths_to_mutate``? (ROADMAP E3.3.)

    Requires ``mutmut`` (``pip install "mutmut>=2.4,<3"`` — pinned below 3.x:
    mutmut 3.3.1 segfaulted on every single mutant in live testing here,
    while 2.5.1 worked correctly; loophole inspects your repo, it doesn't
    install dependencies for you, but it shouldn't recommend a version it
    hasn't actually seen run).

    Deliberately does NOT pass ``--CI``: that flag was verified live to make
    mutmut's own exit code report only "did mutmut run without crashing," not
    "were all mutants killed" — a real run with 3 real survivors still
    exited 0 under --CI. The bare ``mutmut run`` exit code is what actually
    reflects the kill rate: non-zero the moment anything survives, exactly
    the "hard verifier, exit 0 = done" contract this needs to satisfy.

    v1 is intentionally binary (every mutant killed, or fail) — no partial
    kill-rate threshold, matching mutmut's own default semantics rather than
    adding a percentage-parsing surface that could itself drift from reality.

    Known platform gap: mutmut unconditionally opens a PTY (``os.openpty()``)
    to stream test output on every POSIX platform. Verified live: this raises
    ``PermissionError`` under loophole's macOS Seatbelt sandbox, which only
    allowlists fixed devices (``/dev/tty`` etc.), not allocating a fresh PTY
    pair — so this verifier currently only works under Linux (bubblewrap's
    minimal ``/dev`` includes PTY support by convention). Not something
    loophole can quietly work around: granting PTY allocation would be a real
    sandbox-policy expansion made for one third-party tool's convenience, not
    a decision to make casually.
    """
    if not paths_to_mutate or not paths_to_mutate.strip():
        raise ValueError("paths_to_mutate must be a non-empty path")
    parts = ["mutmut", "run", "--paths-to-mutate={}".format(shlex.quote(paths_to_mutate))]
    if tests_dir:
        parts.append("--tests-dir={}".format(shlex.quote(tests_dir)))
    return " ".join(parts)


class ContractError(ValueError):
    """Raised when a Goal Contract is invalid (e.g. has no way to define done)."""


@dataclass
class GoalContract:
    """The full, falsifiable specification of a goal."""
    goal: str                                   # natural-language goal
    verifiers: List[Verifier] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    # Mutation policy:
    allowed_writes: List[str] = field(default_factory=lambda: ["**"])  # glob allowlist
    protected_paths: List[str] = field(default_factory=list)           # goal-wide protected
    # Non-functional requirements recorded for the residual-risk report:
    non_functional: List[str] = field(default_factory=list)
    # Budget ceilings:
    max_cost_usd: float = 0.0                    # 0 = unlimited (local models are free)
    max_tokens: int = 0                          # 0 = unlimited
    max_rounds: int = 25
    timeout_seconds: int = 7200
    # Module SDK firewall: "code" is the strict flagship domain (deterministic gate,
    # only builtin verifiers). A module declares its own domain (e.g. "quant") to
    # use module verifier kinds. The two never mix in one contract.
    domain: str = "code"

    def validate(self) -> None:
        """Reject any contract that cannot define 'done'."""
        if not self.goal or not self.goal.strip():
            raise ContractError("goal text is required")
        if not self.verifiers:
            raise ContractError(
                "a goal with no verifier is rejected: you cannot run-until-done "
                "without defining done. Add at least one --verify command, a soft "
                "rubric, or a human checkpoint."
            )
        # Firewall: the strict "code" domain admits only builtin verifier kinds, so
        # module (graded/probabilistic) verifiers can never weaken the code path.
        if self.domain == "code":
            non_builtin = sorted({v.kind_str for v in self.verifiers if not v.is_builtin})
            if non_builtin:
                raise ContractError(
                    "domain='code' admits only builtin verifiers (hard/soft/human); "
                    "module kinds {} require a non-code domain.".format(non_builtin))
        # A soft verifier may only veto when something can grant; a contract that can
        # never GRANT completion is rejected. Module verifiers can grant (they gate at
        # the round level), so they count toward "can_grant".
        kinds = {v.kind for v in self.verifiers}
        can_grant = (VerifierKind.HARD in kinds or VerifierKind.HUMAN in kinds
                     or any(not v.is_builtin for v in self.verifiers))
        if not can_grant:
            raise ContractError(
                "contract has only soft verifiers, which can VETO but never GRANT "
                "completion. Add a hard (command) verifier or a human checkpoint."
            )

    @property
    def hard_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.HARD]

    @property
    def soft_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.SOFT]

    @property
    def human_verifiers(self) -> List[Verifier]:
        return [v for v in self.verifiers if v.kind == VerifierKind.HUMAN]

    def write_violations(self, changed: List[str],
                         task_writes: Optional[List[str]] = None) -> List[str]:
        """S5: enforce the write allowlist at commit time.

        The executor is *told* to stay within its declared writes; this MAKES it
        true. A changed path is a violation if no allow glob matches it, or if it
        matches a protected path. ``task_writes`` (the task's own declared writes)
        narrows the contract-wide ``allowed_writes``; an empty task list falls back
        to the contract allowlist (default ['**'] = unrestricted). Glob matching
        mirrors the verifier boundary: match on the full repo-relative path or the
        basename.
        """
        allow = [g for g in (task_writes or self.allowed_writes or ["**"]) if g] or ["**"]
        deny = self.all_protected_paths
        out: List[str] = []
        for rel in changed:
            rel = rel.strip()
            if not rel:
                continue
            base = os.path.basename(rel)
            if not any(fnmatch.fnmatch(rel, g) or fnmatch.fnmatch(base, g) for g in allow):
                out.append("{} is outside allowed writes {}".format(rel, allow))
            elif any(fnmatch.fnmatch(rel, d) or fnmatch.fnmatch(base, d) for d in deny):
                out.append("{} is a protected path".format(rel))
        return out

    def auto_protect(self, workspace: str) -> List[str]:
        """Implicitly protect the goalpost itself.

        The contract file and any workspace files a hard verifier executes ARE
        the definition of "done" — an executor that can rewrite them can move
        the goalpost instead of reaching it. `loophole init` protects them by
        convention; this makes it hold for hand-authored contracts too. Scans
        each hard verifier command for tokens that resolve to existing files
        inside the workspace (plus the conventional ./loophole.json) and adds
        them to ``protected_paths``. Returns the newly added repo-relative
        paths so the caller can log them for the audit trail.
        """
        candidates: List[str] = ["loophole.json"]
        for v in self.hard_verifiers:
            try:
                candidates.extend(shlex.split(v.command or ""))
            except ValueError:            # unbalanced quotes — best-effort split
                candidates.extend((v.command or "").split())
        already = set(self.all_protected_paths)
        added: List[str] = []
        for tok in candidates:
            if not tok or os.path.isabs(tok):
                continue                  # boundary globs are repo-relative
            rel = os.path.normpath(tok)
            if rel.startswith("..") or rel in already:
                continue
            if os.path.isfile(os.path.join(workspace, rel)):
                self.protected_paths.append(rel)
                already.add(rel)
                added.append(rel)
        return sorted(added)

    @property
    def all_protected_paths(self) -> List[str]:
        paths = list(self.protected_paths)
        for v in self.verifiers:
            paths.extend(v.protected_paths)
            paths.extend(v.trusted_inputs)
        return sorted(set(paths))

    def to_json(self) -> str:
        return json.dumps(
            {
                "goal": self.goal,
                "verifiers": [v.to_dict() for v in self.verifiers],
                "acceptance_criteria": self.acceptance_criteria,
                "allowed_writes": self.allowed_writes,
                "protected_paths": self.protected_paths,
                "non_functional": self.non_functional,
                "max_cost_usd": self.max_cost_usd,
                "max_tokens": self.max_tokens,
                "max_rounds": self.max_rounds,
                "timeout_seconds": self.timeout_seconds,
                "domain": self.domain,
            },
            indent=2,
        )

    @classmethod
    def from_json(cls, raw: str) -> "GoalContract":
        d = json.loads(raw)
        return cls.from_dict(d)

    @classmethod
    def from_dict(cls, d: dict) -> "GoalContract":
        verifiers = [Verifier.from_dict(v) for v in d.get("verifiers", [])]
        return cls(
            goal=d["goal"],
            verifiers=verifiers,
            acceptance_criteria=d.get("acceptance_criteria", []),
            allowed_writes=d.get("allowed_writes", ["**"]),
            protected_paths=d.get("protected_paths", []),
            non_functional=d.get("non_functional", []),
            max_cost_usd=d.get("max_cost_usd", 0.0),
            max_tokens=d.get("max_tokens", 0),
            max_rounds=d.get("max_rounds", 25),
            timeout_seconds=d.get("timeout_seconds", 7200),
            domain=d.get("domain", "code"),
        )

    @classmethod
    def quick(
        cls,
        goal: str,
        verify_cmd: Optional[str] = None,
        human: bool = False,
        **kwargs: Any,
    ) -> "GoalContract":
        """Build a minimal contract from CLI-style inputs."""
        verifiers: List[Verifier] = []
        if verify_cmd:
            verifiers.append(Verifier(kind=VerifierKind.HARD, command=verify_cmd))
        if human or not verify_cmd:
            verifiers.append(
                Verifier(kind=VerifierKind.HUMAN, prompt="Does the result satisfy: " + goal + "?")
            )
        c = cls(goal=goal, verifiers=verifiers, **kwargs)
        c.validate()
        return c
