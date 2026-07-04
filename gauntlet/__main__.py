"""``python -m gauntlet`` — run every reward-hacking scenario against the real
loophole CLI and print a shareable, human-readable report. Exit code is 0 iff
every scenario was caught (so this doubles as a CI gate, not just a report).
"""
from __future__ import annotations

import sys

from gauntlet.scenarios import ALL_SCENARIOS, run_scenario


def main() -> int:
    print("=" * 72)
    print("loophole reward-hacking gauntlet")
    print("=" * 72)
    print()
    print("No live model required to run this: a fake planner stands in for the")
    print("LLM that would normally plan the work, and one scenario's soft-verifier")
    print("judge is a scripted stand-in (deterministic, labeled below) rather than")
    print("a real LLM call — see gauntlet/README.md for what that scenario proves")
    print("and doesn't.")
    print()

    results = []
    for sc in ALL_SCENARIOS:
        r = run_scenario(sc)
        results.append(r)
        mark = "✓ CAUGHT" if r.caught else "✗ MISSED"
        print("[{}] {}".format(mark, sc.title))
        print("      pattern: {}".format(sc.real_world_pattern))
        if sc.citation:
            print("      cites:   {}".format(sc.citation))
        print("      verdict: {}".format(r.detail))
        print()

    caught = sum(1 for r in results if r.caught)
    total = len(results)
    print("-" * 72)
    print("{}/{} cheats caught".format(caught, total))
    print("-" * 72)

    return 0 if caught == total else 1


if __name__ == "__main__":
    sys.exit(main())
