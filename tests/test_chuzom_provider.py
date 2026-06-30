"""The `chuzom:` provider — Chuzom-policy model selection, with LoopHole's verifier
boundary still the authority on 'done'."""
from __future__ import annotations

from loophole import provider as P
from loophole.provider import ChuzomProvider, Completion, Msg, make_provider


def test_make_provider_chuzom_tiers():
    assert isinstance(make_provider("chuzom"), ChuzomProvider)
    assert make_provider("chuzom").tier == "auto"
    assert make_provider("chuzom:complex").tier == "complex"
    assert make_provider("chuzom:simple").tier == "simple"


def test_auto_tier_picks_by_prompt_size():
    p = ChuzomProvider("auto")
    assert p._pick([Msg(role="user", content="hi")])[0] == "simple"
    assert p._pick([Msg(role="user", content="x" * 3000)])[0] == "moderate"
    assert p._pick([Msg(role="user", content="x" * 9000)])[0] == "complex"


def test_fixed_tier_overrides_heuristic():
    p = ChuzomProvider("complex")
    tier, spec = p._pick([Msg(role="user", content="tiny")])
    assert tier == "complex" and "qwen3-coder:30b" in spec


def test_routes_to_selected_model_and_tags_it(monkeypatch):
    captured = {}

    class _Fake(P.Provider):
        name = "fake"

        def __init__(self, spec):
            self.model = spec

        def complete(self, msgs, tools=None, max_tokens=4096, temperature=0.2):
            captured["spec"] = self.model
            captured["tools"] = tools
            return Completion(text="ok", model=self.model)

    monkeypatch.setattr(P, "make_provider", lambda spec: _Fake(spec))
    out = ChuzomProvider("complex").complete([Msg(role="user", content="do it")],
                                             tools=[{"name": "x"}])
    assert out.text == "ok"
    assert captured["spec"] == "ollama:qwen3-coder:30b"     # complex tier model
    assert captured["tools"] == [{"name": "x"}]             # tool-calling passes through
    assert out.model.startswith("chuzom:complex->")


def test_env_overrides_tier_models(monkeypatch):
    monkeypatch.setenv("CHUZOM_TIER_SIMPLE", "anthropic:claude-haiku-4-5")
    p = ChuzomProvider("simple")
    assert p._pick([Msg(role="user", content="hi")])[1] == "anthropic:claude-haiku-4-5"
