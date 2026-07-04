**What does this change, and why?**

**Test plan**

- [ ] Added/updated a test that would have failed before this change
- [ ] `LOOPHOLE_TEST_PROFILE=core pytest -q` passes
- [ ] `pytest -q` (full suite) passes locally, if this touches sandboxing,
      verifiers, the merge gate, or secret handling
- [ ] If this adds a verifier, executor adapter, or CLI flag: a template
      contract or README line demonstrates it

**Does this touch the verifier boundary, the sandbox, or anything that
decides what counts as "done"?** If so, say how you thought about an agent
routing around it — see [CONTRIBUTING.md](../CONTRIBUTING.md#review-culture).
