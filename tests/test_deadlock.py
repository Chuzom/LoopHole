"""Fast structural-deadlock detection.

When no single task's merge can pass the hard verifier(s), HEAD never advances and
the run should PAUSE with an actionable diagnostic within a few rounds — not churn to
max_rounds. The planner here VARIES its plan each round (as a real LLM planner does),
so degenerate-plan detection never fires; only the HEAD-didn't-advance signal catches it.
"""
import json
import os
import subprocess
import tempfile

from click.testing import CliRunner

from loophole.cli import main
from loophole.provider import Provider, Completion


class _VaryingPlanner(Provider):
    name = "fake"

    def __init__(self):
        self.n = 0

    def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
        self.n += 1
        # plan_hash is STRUCTURAL (writes + dep-shape + read count), so vary the read
        # count each round => a structurally distinct plan every time => degenerate-plan
        # detection can't fire. Only the HEAD-didn't-advance deadlock signal can catch it
        # (this mirrors a real planner that keeps re-splitting the work differently).
        tasks = [{"id": "t1", "description": "attempt {}".format(self.n),
                  "depends_on": [], "reads": ["r{}".format(i) for i in range(self.n)],
                  "writes": ["m.py"]}]
        return Completion(text=json.dumps(tasks))


def _git_ws():
    ws = tempfile.mkdtemp(prefix="loophole_deadlock_")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", ws], check=True)
    subprocess.run(["git", "-C", ws, "commit", "-q", "--allow-empty", "-m", "init"],
                   check=True, env=env)
    return ws


def test_merge_gate_deadlock_pauses_fast(monkeypatch, tmp_path):
    monkeypatch.setattr("loophole.cli.make_provider", lambda spec: _VaryingPlanner())
    ws = _git_ws()
    db = os.path.join(str(tmp_path), "state.db")
    # The task writes a file (so there IS a merge candidate), but the hard verifier
    # `false` always fails => every candidate is rejected by the R3 gate => HEAD never
    # advances. max-rounds 12 is well above the deadlock pause threshold (5).
    write_file = "python3 -c \"open('m.py','w').write('x=1')\""
    r = CliRunner().invoke(main, [
        "run", "port a module", "--verify", "false", "--workspace", ws,
        "--executor-command", write_file, "--skip-critique", "--no-watch",
        "--max-rounds", "12", "--db", db,
    ])
    assert r.exit_code == 1, r.output                      # paused = not done
    assert "structural deadlock" in r.output.lower(), r.output
    assert "max rounds" not in r.output.lower()            # caught FAST, not at the cap
    # Rounds used should be the pause threshold (5), far below max_rounds (12).
    assert "Rounds used: 5" in r.output, r.output
