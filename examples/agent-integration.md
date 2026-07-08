# Using loophole from your coding agent

Call loophole from inside Claude Code, Codex, or any agent harness — with a live ASCII
progress view — via a `/loophole` slash command.

## The idea

Agent harnesses **capture** a command's stdout, so loophole's live terminal animation never
renders inside them. Instead, the agent calls loophole's **MCP tools** and re-prints a clean
markdown snapshot as it polls: `loophole_run` starts a run in the background and returns a
goal id; `loophole_status` returns the current snapshot (goal, per-agent task table,
milestones, verdict). The run keeps progressing between calls, and loophole — not the agent —
decides "done".

## Register the MCP server

`loophole mcp` is a zero-dependency stdio MCP server. Install loophole so it's on PATH:

```bash
pip install loophole-agents
```

**Claude Code:**

```bash
claude mcp add loophole -- loophole mcp
```

or add to a project `.mcp.json`:

```json
{ "mcpServers": { "loophole": { "command": "loophole", "args": ["mcp"] } } }
```

From a source checkout (editable install), use `python -m loophole mcp` — or point `command`
at the venv's `loophole` — so you exercise the checked-out CLI rather than the published one.

**Codex:** register the same `loophole mcp` server in your Codex MCP config.

## The `/loophole` command

Two ready-made command templates live in [`examples/agents/`](agents/):

- Copy [`claude-code-loophole.md`](agents/claude-code-loophole.md) to
  `.claude/commands/loophole.md` (project) or `~/.claude/commands/loophole.md` (personal).
- Copy [`codex-loophole.md`](agents/codex-loophole.md) to `~/.codex/prompts/loophole.md`.

Then:

```
/loophole create add.py with add(a,b) || python3 -c "from add import add; assert add(2,3)==5"
```

The agent calls `loophole_run`, then polls `loophole_status` every few seconds, printing the
updated snapshot until it reaches ✅ VERIFIED DONE (or ❌ / ⏸️).

## Generic recipe (no MCP)

Any agent can drive loophole through the CLI. Runs share a SQLite DB, so a second process can
follow one live:

```bash
loophole run "<goal>" --verify '<cmd>' --workspace ./out &   # starts in the background
loophole status <goal-id>      # print a snapshot (repeat every few seconds)
# or: loophole watch <goal-id> # follow the milestone stream
```

Re-print each snapshot to give the user a progress view.

## Privileges

A mission asks **once** how much freedom loophole gets:
`--privileges full|guarded|locked` (or an interactive prompt in a terminal). `guarded` is the
default — OS-sandboxed, network denied by default, and side-effecting actions (push / publish /
deploy) are **allowed but recorded** in the Residual-Risk Report. `locked` denies network and
routes completion to a human; `full` runs trusted with network. See the README for details.
