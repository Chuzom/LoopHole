# Why loophole exists

I kept watching AI coding agents say "done" when they weren't.

Not maliciously — models are trained to please, and "all tests pass!" is a
satisfying thing to say whether or not it's true. I'd give an agent a real
task, walk away, and come back to one of three outcomes: it quit after one
plausible-looking pass; it claimed success while having quietly deleted the
tests that were failing; or it just had no way to know it wasn't finished,
so it either stopped too early or looped forever. None of that is a model
problem. It's a structural one — nothing in the loop *external to the
agent* gets to decide whether the goal is actually met. So I built that
external thing: a goal becomes a contract, a contract is checked by
something the agent can't talk its way around, and the agent doesn't get a
vote.

That's the pitch. Here's the part I didn't expect: building the tool whose
entire job is "don't trust a claim, verify it" did not make me immune to
shipping unverified claims. It made the opposite bugs *more* interesting,
because I kept catching myself doing the exact thing loophole exists to
catch agents doing.

## The verifier that couldn't reach `localhost`

Early on I added `--verify-http` — point it at a URL, it retries a `curl`
a few times, and only a real matching HTTP status counts as done. I wrote
the command-builder, unit-tested it thoroughly, and it was correct. Then I
ran it end to end against a real local server and it failed every time —
not because the server was down, but because loophole's own sandbox denies
network access by default for every verifier, and the generated `curl`
command had never been told to opt in. The unit tests were fine; they
tested the string `curl` produces, not whether that string could actually
reach anything. The bug wasn't in the feature. It was in the gap between
"the code is correct" and "the code does the thing," and no amount of
unit-testing the command-builder in isolation would have caught it — only
running it, for real, against a real target did.

## The routing layer that kept a memory of its own mistake

Separately, I found a bug in Chuzom (the cost-routing layer loophole uses
for model calls): a session buffer recorded every prompt and response it
routed, verbatim, into an always-on cache meant to give future prompts
useful context. Once, an experimental feature injected a block of
extra context into a routed prompt. That block got captured into the
buffer along with everything else — and kept getting replayed into
*future* prompts indefinitely, even after the feature that caused it was
turned back off. The system had no mechanism to distinguish "context I was
told to remember" from "content I was fed once and should never repeat."
It's the same failure shape as an agent that edits its own test to make a
red build look green: something got into the record that shouldn't count,
and nothing was checking.

## The executor whose "success" was the problem

The aider adapter has a one-line footgun buried in its flags:
`--no-auto-commits`. Aider commits your changes for you by default — a
reasonable thing for a standalone tool to do, and exactly wrong for a
tool like loophole that decides "did anything actually change" by looking
at `git diff --cached` after the executor runs. Without that flag, aider
would commit its own work, loophole would see a clean working tree, and
report "no file changes" — the executor's *own success* (it committed!)
would look, from loophole's side, identical to an agent that did nothing
at all. I only found this by reading aider's actual behavior against a
real binary, not by assuming a well-known tool would behave the way I
expected.

## What all three have in common

None of these were caught by a plausible-sounding check. They were caught
by actually running the thing against something real and looking hard at
what came back — which is, uncomfortably, the exact discipline loophole
exists to enforce on agents. I don't think that's a coincidence, and I
don't think it's specific to me. It's what happens whenever the entity
doing the work is also the one deciding if the work is done. loophole's
whole architecture is a bet that the fix is structural, not vigilance:
put a real, external, falsifiable check between "I did it" and "it's
accepted," and make it check the actual result — a passing test, a real
HTTP response, a real diff — not a description of the result.

That's also why loophole's own test-count badge went stale by 93 tests
before I noticed, and why a secret-scrub check I'd built for one output
path (a PR comment) turned out to have a gap in a completely different
one (`--json-file`) that nobody had checked. I didn't catch either by
being more careful. I caught them by building a check that runs
regardless of how careful I feel — the same medicine, aimed at myself.
