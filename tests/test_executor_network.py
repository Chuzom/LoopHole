"""Phase 1 — scoped executor egress + secret pass-through, so an API-calling
framework agent can reach ITS api without dropping the sandbox."""
from __future__ import annotations

import os
import tempfile

import loophole.tools as T
from loophole.sandbox import SandboxPolicy, _seatbelt_profile, scrub_env


# ---- Seatbelt per-host scoping ---------------------------------------------

def _profile(**kw):
    return _seatbelt_profile(tempfile.mkdtemp(), SandboxPolicy(**kw))


def test_seatbelt_allows_network_when_enabled():
    # without a proxy (e.g. a verifier's allow_network), egress stays
    # all-or-nothing — and the profile must stay VALID (no bogus
    # `(remote tcp "host")` that sandbox-exec rejects).
    prof = _profile(allow_network=True, allowed_hosts=("api.anthropic.com",))
    assert "(allow network*)" in prof
    assert "remote tcp" not in prof            # not the invalid per-host syntax
    assert "(allow network*)" in _profile(allow_network=True)


def test_seatbelt_with_proxy_is_localhost_only():
    # with the egress proxy, the jail may reach ONLY localhost — real egress
    # goes through the host-allowlisted proxy outside the jail.
    prof = _profile(allow_network=True, allowed_hosts=("api.anthropic.com",),
                    proxy_port=54321)
    assert "(allow network*)" not in prof
    assert '(allow network-outbound (remote ip "localhost:*"))' in prof


def test_seatbelt_denies_by_default():
    p = _profile()
    assert "(deny network*)" in p and "(allow network*)" not in p


# ---- secret pass-through ----------------------------------------------------

def test_scrub_env_keeps_only_whitelisted_secret(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "keep-me")
    monkeypatch.setenv("OPENAI_API_KEY", "drop-me")
    env = scrub_env({}, keep=("ANTHROPIC_API_KEY",))
    assert env.get("ANTHROPIC_API_KEY") == "keep-me"   # the executor's key survives
    assert "OPENAI_API_KEY" not in env                 # every other secret scrubbed


def test_scrub_env_default_drops_all_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert "ANTHROPIC_API_KEY" not in scrub_env({})    # no keep -> scrubbed


# ---- plumbing: Toolbelt -> SandboxPolicy ------------------------------------

def test_toolbelt_threads_network_and_secrets_into_policy(monkeypatch):
    captured = {}

    def _fake_wrap(command, root, policy):
        captured["policy"] = policy
        raise T.SandboxUnavailable("stop-here")        # short-circuit run_shell

    monkeypatch.setattr(T, "wrap", _fake_wrap)
    T.Toolbelt("/tmp", network_hosts=("api.anthropic.com",),
               pass_env=("ANTHROPIC_API_KEY",)).run_shell("echo hi")
    p = captured["policy"]
    assert p.allow_network is True
    assert p.allowed_hosts == ("api.anthropic.com",)
    assert p.pass_env == ("ANTHROPIC_API_KEY",)


def test_command_executor_forwards_policy():
    from loophole.executor import CommandExecutor
    ex = CommandExecutor("claude -p {task}", network_hosts=("api.anthropic.com",),
                         pass_env=("ANTHROPIC_API_KEY",))
    assert ex.network_hosts == ("api.anthropic.com",)
    assert ex.pass_env == ("ANTHROPIC_API_KEY",)


# ---- live: enabling network actually works + profile compiles ---------------

def test_network_enabled_reaches_a_host_and_confines_fs():
    import pytest
    from loophole.sandbox import mechanism
    if mechanism() == "none":
        pytest.skip("no OS sandbox on this host")
    tb = T.Toolbelt("/tmp", timeout=15, network_hosts=("example.com",))
    r = tb.run_shell('curl -s -m 8 -o /dev/null -w "%{http_code}" https://example.com')
    if not (r.ok and "200" in r.output):
        # environments without sandboxed outbound network/DNS (e.g. locked-down CI)
        # can't exercise this smoke test — the profile-string tests cover the rest.
        pytest.skip("sandboxed outbound network/DNS unavailable here: " + (r.output or "")[:80])
    # network reachable when allowed -> confirm it's DENIED by default (no hosts)
    tb2 = T.Toolbelt("/tmp", timeout=10)
    r2 = tb2.run_shell('curl -s -m 6 -o /dev/null -w "%{http_code}" https://example.com')
    assert not r2.ok or "200" not in r2.output
    # and that the allowlist actually SCOPES: a non-allowlisted host is refused
    # by the egress proxy (Seatbelt path) and the denial is captured for audit
    from loophole.sandbox import mechanism as _mech
    if _mech() == "seatbelt":
        r3 = tb.run_shell(
            'curl -s -m 8 -o /dev/null -w "%{http_code}" https://www.google.com')
        assert "200" not in r3.output, "non-allowlisted host escaped the proxy"
        assert "www.google.com" in tb.network_denials
