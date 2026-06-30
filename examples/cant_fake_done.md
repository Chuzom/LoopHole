# Example: the "can't-fake-done" demo

The one-line pitch for loophole, runnable in one command — **no LLM required**
(it drives the real verifier boundary directly, so it's deterministic):

```bash
python examples/cant_fake_done.py
```

What it shows:

1. **A naive agent declares success** on an `add(a, b)` that's actually buggy
   (`return a - b`): *"Done! ✅"*
2. **loophole refuses.** It runs the contract's hard verifier against a fresh
   checkout, the check fails, and the Residual-Risk Report says **OUTCOME: FAILED**
   with the precise reason. The false claim is caught.
3. **loophole accepts only after the bug is really fixed** (`return a + b`) and the
   verifier passes — **OUTCOME: DONE**.

```
[1] A naive agent simply declares success:
    🤖  "Done! I've implemented add(). ✅"
    ...but the acceptance check actually FAILS on this code.

[2] loophole refuses to take the agent's word — it runs the
    contract's hard verifier against a fresh checkout:
    [FAIL] hard:python3 -c "...assert add(2,3)==5..."   -> exit 1
    → loophole does NOT declare done. The false claim is caught.

[3] Now we actually fix the bug (return a + b) and re-verify:
    [PASS] hard:python3 -c "...assert add(2,3)==5..."
    → Only now does loophole accept completion.
```

**Takeaway:** in loophole, *"done" means the verifier passed — not that an LLM said
so.* That's the whole difference from an agent that grades its own homework.
