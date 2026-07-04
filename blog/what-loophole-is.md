# What loophole actually is

Here's the situation loophole exists for, without any jargon in it yet.

You hire a contractor to fix your roof. They finish, take a photo from the
ground, and text you: "Done! Looks great." Do you pay them, or do you climb
up and look? Most people climb up and look — not because they distrust the
contractor personally, but because "looks great" and "actually doesn't
leak" are different claims, and only one of them matters.

That's the whole idea. AI coding agents are contractors who are very good
at saying "done." They're trained to be helpful and confident, and a
confident "all tests pass!" is a satisfying sentence to produce whether or
not it's true. loophole is the person who climbs up and looks — except
instead of a person, it's a real, mechanical check that can't be talked
out of its answer.

## What it does, concretely

You tell loophole two things: a goal ("build a login page," "fix this
bug"), and what "actually done" means for that goal — a test suite that
has to pass, a web page that has to load, a specific thing that has to be
true. loophole then hands the goal to an AI agent — any agent: Claude,
Codex, aider, whatever you already use — lets it work in a sealed-off
copy of your project where it can't touch anything it shouldn't, and waits.

When the agent says it's finished, loophole doesn't take its word for it.
It runs the real check. Not "did the agent claim tests pass" — did the
tests *actually* pass, right now, for real. If the agent tried something
sneaky — deleting an inconvenient failing test, quietly loosening the
rule so anything counts as a pass — loophole catches that too, because it
looks at what changed, not just what the agent reported. If the check
fails, loophole sends the agent back to try again. Only a real pass gets
through.

## What you get at the end

A yes/no answer you don't have to take on faith, plus a plain-language
report of exactly what was checked and what wasn't — so you know not just
*that* it's done, but *why* you can believe it. For a developer, that
means a PR that's already been proven against your own test suite before
you spend your attention reading it. For a team, it means a GitHub check
that blocks a merge on a real result instead of an agent's self-report.
For anyone directing an AI agent at real work, it means the gap between
"the agent said it worked" and "it actually works" gets closed by a
machine, before it becomes your problem.

loophole doesn't write your code. It's the part that makes sure "done"
means something.
