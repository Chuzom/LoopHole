"""VIS-4 — guard the repositioning artifacts (examples + README positioning)."""
from __future__ import annotations

import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_ci_gate_example_exists_and_describes_the_gate():
    txt = _read("examples", "ci_gate.md")
    assert "loophole" in txt
    assert "loophole-gate" in txt or "acceptance" in txt.lower()
    assert "--executor-command" in txt          # bring-your-own-executor shown
    assert "loophole audit" in txt              # the trust artifact is referenced


def test_readme_carries_the_acceptance_layer_positioning():
    rm = _read("README.md")
    assert "CI for AI agents" in rm
    assert "Bring your own executor" in rm
    assert "examples/ci_gate.md" in rm
    # the stale "sandbox not done" warning must be gone (sandbox shipped)
    assert "OS-level sandboxing of those commands is on the roadmap, not done" not in rm
