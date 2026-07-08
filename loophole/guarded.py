"""Classify guarded shell actions for audit logging.

A "guarded" action is a side-effecting / irreversible command an agent might run:
deploy, release, publish, push, or purchase. loophole records these in the
Residual-Risk Report (and the audit trail) so a run's real-world effects are never
silent. Detection is by MECHANISM (the command's verb), not intent — see
``classify_guarded_action``.
"""
import re
import shlex
from typing import Optional

_GUARDED_ACTION_PATTERNS = [
    (re.compile(r"^(?:git|hub)\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+push\b"), "vcs-push"),
    (re.compile(r"^(?:npm|yarn|pnpm)\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+publish\b"), "publish"),
    (re.compile(r"^twine\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+upload\b"), "publish"),
    (re.compile(r"^python(?:\d+(?:\.\d+)?)?\s+-m\s+twine\s+upload\b"), "publish"),
    (re.compile(r"^poetry\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+publish\b"), "publish"),
    (re.compile(r"^cargo\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+publish\b"), "publish"),
    (re.compile(r"^gem\b(?:\s+(?:-[^\s]+|--[^\s]+)(?:\s+\S+)?)?\s+push\b"), "publish"),
    (re.compile(r"^mvn\b.*\bdeploy\b"), "publish"),
    (re.compile(r"^gradle\b.*\bpublish\b"), "publish"),
    (re.compile(r"^dotnet\s+nuget\s+push\b"), "publish"),
    (re.compile(r"^gh\s+release\s+(?:create|upload)\b"), "release"),
    (re.compile(r"^goreleaser\s+release\b"), "release"),
    (re.compile(r"^docker\s+(?:compose\s+)?push\b"), "deploy"),
    (re.compile(r"^kubectl\s+(?:apply|rollout)\b"), "deploy"),
    (re.compile(r"^helm\s+(?:install|upgrade)\b"), "deploy"),
    (re.compile(r"^terraform\s+(?:apply|destroy)\b"), "deploy"),
    (re.compile(r"^(?:serverless|sls|fly|flyctl)\s+deploy\b"), "deploy"),
    (re.compile(r"^vercel\b(?:.*\s--prod\b|\s+deploy\b)"), "deploy"),
    (re.compile(r"^netlify\s+deploy\b"), "deploy"),
    (re.compile(r"^heroku\b.*\b(?:deploy|releases?)\b"), "deploy"),
    (re.compile(r"^aws\b.*\b(?:deploy|s3\s+(?:sync|cp)|cloudformation\s+deploy|lambda\s+update)\b"), "deploy"),
    (re.compile(r"^gcloud\b.*\b(?:deploy|app\s+deploy|run\s+deploy)\b"), "deploy"),
    (re.compile(r"^az\b.*\b(?:webapp|deployment)\b"), "deploy"),
    (re.compile(r"^stripe\b"), "purchase"),
    (re.compile(r"^(?:curl|http|wget)\b.*\b(?:api\.stripe\.com|paypal|checkout|billing)\b", re.IGNORECASE), "purchase"),
]


def classify_guarded_action(command: str) -> Optional[str]:
    """Return a short category for a side-effecting command, else None.

    Categories: ``vcs-push`` | ``publish`` | ``release`` | ``deploy`` | ``purchase``.
    Robust to leading paths, ``sudo``, ``VAR=val`` prefixes, extra flags, and command
    chains (``&&``, ``||``, ``;``, ``|``) — the first matched segment wins.
    """
    def normalize(segment: str) -> str:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()

        while tokens and tokens[0] == "sudo":
            tokens = tokens[1:]
        while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", tokens[0]):
            tokens = tokens[1:]

        if not tokens:
            return ""

        tokens[0] = tokens[0].rsplit("/", 1)[-1]
        return " ".join(tokens)

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        parts = list(lexer)
    except ValueError:
        parts = re.split(r"\s*(?:&&|\|\||[;|])\s*", command)

    segments = []
    current = []
    for part in parts:
        if part in {"&&", "||", ";", "|"} or set(part) <= {"&", "|", ";"}:
            if current:
                segments.append(" ".join(current))
                current = []
        else:
            current.append(part)
    if current:
        segments.append(" ".join(current))

    if not segments:
        segments = re.split(r"\s*(?:&&|\|\||[;|])\s*", command)

    for segment in segments:
        normalized = normalize(segment.strip())
        if not normalized:
            continue
        for pattern, category in _GUARDED_ACTION_PATTERNS:
            if pattern.search(normalized):
                return category

    return None
