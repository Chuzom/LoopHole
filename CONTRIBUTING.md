# Contributing to loophole

Issues and PRs are welcome. This file is the "how," not the "why" — for the
architecture and design rationale, read [README.md](README.md) and
[ROADMAP.md](ROADMAP.md) first.

## Dev setup

```bash
git clone https://github.com/Chuzom/loophole.git
cd loophole
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
```

## Test profiles

The suite is split into two profiles:

- **Core** (`LOOPHOLE_TEST_PROFILE=core pytest -q`) — fast, deterministic,
  no OS sandbox or localhost sockets required. This is what CI runs on every
  push/PR across Python 3.9/3.11/3.12 (`.github/workflows/ci.yml`).
- **Full** (`pytest -q`) — everything, including real sandbox-escape attempts
  under Seatbelt (macOS) or bubblewrap (Linux), and tests that bind local
  HTTP servers. CI runs this once under real bwrap on Linux
  (`.github/workflows/sandbox.yml`), gated at ≥75% coverage.

Run both locally before opening a PR if your change touches sandboxing,
verifiers, or the merge gate. If you're only touching docs or a template,
the core profile is enough.

## What a PR should include

- A test that would have failed before your change (a regression test for a
  bug fix, a new test for a new verifier/adapter/flag).
- If you're adding a verifier, executor adapter, or CLI flag: a template
  contract or a doc line demonstrating it, matching the pattern of the
  existing ones in `loophole/templates/` and the CLI reference block in
  README.md.
- A commit message that says *why*, not just *what* — especially for
  anything touching the verifier boundary, the sandbox, or secret handling.
  Reviewers (and the adversarial-council review style below) will ask.

## Review culture

The architecture was designed — and is periodically re-audited — by a
multi-model council whose job is to find the gap, not to rubber-stamp the
design. That's the project's default review posture for everything, not
just architecture: bring disagreement, and expect the same in return. If a
change touches the verifier boundary, the sandbox, or anything that decides
what counts as "done," assume it will be scrutinized for how an agent could
route around it — that scrutiny is the product.

## Reporting a security issue

Please don't open a public issue for a sandbox-escape, verifier-bypass, or
secret-leak finding. Email the maintainer (see the GitHub profile on this
repo) or open a private security advisory via GitHub's "Report a
vulnerability" flow instead.
