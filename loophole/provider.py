"""Provider abstraction.

The interface puts tool-calling in the contract: providers must be able to take
a list of tool schemas and (optionally) emit tool calls. Concrete providers:
Ollama (default, local, free), Anthropic, OpenAI. They are imported lazily so
the core package has zero hard dependencies beyond click.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Msg:
    role: str          # system|user|assistant|tool
    content: str
    name: Optional[str] = None          # tool name (for role=tool)
    tool_call_id: Optional[str] = None

    def to_openai(self) -> dict:
        d: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class Completion:
    text: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Provider(ABC):
    """Tool-calling LLM provider."""

    name: str = "provider"
    model: str = ""
    # rough $/1k tokens (input, output); 0 for local/free models
    price_in: float = 0.0
    price_out: float = 0.0

    @abstractmethod
    def complete(self, msgs: List[Msg], tools: Optional[List[dict]] = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> Completion:
        ...

    def cost(self, c: Completion) -> float:
        return (c.prompt_tokens / 1000.0) * self.price_in + \
               (c.completion_tokens / 1000.0) * self.price_out


class ProviderError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Ollama (default)                                                            #
# --------------------------------------------------------------------------- #
class OllamaProvider(Provider):
    name = "ollama"
    price_in = 0.0
    price_out = 0.0

    def __init__(self, model: str = "llama3", base_url: Optional[str] = None):
        self.model = model
        self.base_url = (base_url or os.environ.get("OLLAMA_BASE_URL")
                         or "http://localhost:11434").rstrip("/")

    def complete(self, msgs: List[Msg], tools: Optional[List[dict]] = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai() for m in msgs],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if tools:
            payload["tools"] = tools
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.URLError as e:
            raise ProviderError(
                "Ollama request failed ({}). Is `ollama serve` running at {}?"
                .format(e, self.base_url))
        message = body.get("message", {})
        tool_calls: List[ToolCall] = []
        for i, tc in enumerate(message.get("tool_calls", []) or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", "call_{}".format(i)),
                name=fn.get("name", ""), arguments=args))
        return Completion(
            text=message.get("content", "") or "",
            tool_calls=tool_calls,
            prompt_tokens=body.get("prompt_eval_count", 0),
            completion_tokens=body.get("eval_count", 0),
            model=self.model,
        )


# --------------------------------------------------------------------------- #
# Anthropic                                                                   #
# --------------------------------------------------------------------------- #
class AnthropicProvider(Provider):
    name = "anthropic"
    # Sonnet-class default pricing per 1k tokens
    price_in = 0.003
    price_out = 0.015

    def __init__(self, model: str = "claude-sonnet-4-6", api_key: Optional[str] = None):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ProviderError("anthropic not installed: pip install 'loophole[anthropic]'") from e
        import anthropic
        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(self, msgs: List[Msg], tools: Optional[List[dict]] = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> Completion:
        system = "\n\n".join(m.content for m in msgs if m.role == "system")
        conv = []
        for m in msgs:
            if m.role == "system":
                continue
            role = "assistant" if m.role == "assistant" else "user"
            conv.append({"role": role, "content": m.content})
        kwargs: Dict[str, Any] = dict(model=self.model, max_tokens=max_tokens,
                                      temperature=temperature, messages=conv)
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [_to_anthropic_tool(t) for t in tools]
        resp = self._client.messages.create(**kwargs)
        text_parts, tool_calls = [], []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name,
                                           arguments=dict(block.input)))
        return Completion(
            text="".join(text_parts), tool_calls=tool_calls,
            prompt_tokens=resp.usage.input_tokens,
            completion_tokens=resp.usage.output_tokens, model=self.model)


def _to_anthropic_tool(t: dict) -> dict:
    fn = t.get("function", t)
    return {"name": fn["name"], "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}})}


# --------------------------------------------------------------------------- #
# OpenAI                                                                      #
# --------------------------------------------------------------------------- #
class OpenAIProvider(Provider):
    name = "openai"
    price_in = 0.005
    price_out = 0.015

    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None):
        try:
            import openai  # noqa: F401
        except ImportError as e:
            raise ProviderError("openai not installed: pip install 'loophole[openai]'") from e
        import openai
        self.model = model
        self._client = openai.OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

    def complete(self, msgs: List[Msg], tools: Optional[List[dict]] = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> Completion:
        kwargs: Dict[str, Any] = dict(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            messages=[m.to_openai() for m in msgs])
        if tools:
            kwargs["tools"] = tools
        resp = self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0].message
        tool_calls = []
        for tc in (choice.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))
        u = resp.usage
        return Completion(
            text=choice.content or "", tool_calls=tool_calls,
            prompt_tokens=u.prompt_tokens if u else 0,
            completion_tokens=u.completion_tokens if u else 0, model=self.model)


# --------------------------------------------------------------------------- #
# Factory                                                                     #
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Chuzom — cost-optimized routing across tiers (local-first)                  #
# --------------------------------------------------------------------------- #
class ChuzomProvider(Provider):
    """Route each call through Chuzom's model-selection POLICY.

    Spec: ``chuzom`` or ``chuzom:<tier>`` with tier in {auto, simple, moderate,
    complex}. The idea: give a cheap model the easy roles (planning) and a stronger
    model the hard ones (execution), then let LoopHole's verifier boundary catch a
    too-cheap pick — Chuzom's cost savings WITH a correctness gate.

    Transport, in order:
      1. HTTP  — if ``$CHUZOM_URL`` is set, POST {prompt, complexity} to a live
         Chuzom router (text-only rounds); fully decoupled from this process.
      2. local — Chuzom's tier->spec policy, delegating to the concrete provider
         (Ollama by default). Works offline and supports tool-calling.

    Each tier maps to a full provider spec, overridable via env
    CHUZOM_TIER_SIMPLE / _MODERATE / _COMPLEX (default: the local Ollama coders
    Chuzom routes to). So a tier can even point at an API model when configured.
    """
    name = "chuzom"

    def __init__(self, tier: str = "auto"):
        self.tier = (tier or "auto").lower()
        self.model = "chuzom:" + self.tier
        self._tiers = {
            "simple": os.environ.get("CHUZOM_TIER_SIMPLE", "ollama:qwen2.5-coder:7b"),
            "moderate": os.environ.get("CHUZOM_TIER_MODERATE", "ollama:qwen3-coder:30b"),
            "complex": os.environ.get("CHUZOM_TIER_COMPLEX", "ollama:qwen3-coder:30b"),
        }
        self._last: Optional[Provider] = None

    def _pick(self, msgs: List[Msg]) -> tuple:
        tier = self.tier
        if tier not in self._tiers:                 # 'auto'/unknown -> size heuristic
            n = sum(len(m.content or "") for m in msgs)
            tier = "simple" if n < 1500 else ("moderate" if n < 6000 else "complex")
        return tier, self._tiers[tier]

    def complete(self, msgs: List[Msg], tools: Optional[List[dict]] = None,
                 max_tokens: int = 4096, temperature: float = 0.2) -> Completion:
        tier, spec = self._pick(msgs)
        url = os.environ.get("CHUZOM_URL")
        if url:                                     # route through the live Chuzom endpoint
            try:                                    # (text AND tool-calling rounds)
                return self._http(url, msgs, tier, max_tokens, tools=tools,
                                  temperature=temperature)
            except Exception:
                pass                                # fall back to local policy routing
        delegate = make_provider(spec)
        self._last = delegate
        c = delegate.complete(msgs, tools=tools, max_tokens=max_tokens, temperature=temperature)
        c.model = "chuzom:{}->{}".format(tier, c.model or spec)
        return c

    def _http(self, url: str, msgs: List[Msg], tier: str, max_tokens: int,
              tools: Optional[List[dict]] = None, temperature: Optional[float] = None) -> Completion:
        # Tool rounds send messages+tools (endpoint's tool path); text rounds send a
        # prompt (endpoint's full route_and_call path).
        if tools:
            payload: Dict[str, Any] = {"messages": [m.to_openai() for m in msgs],
                                       "tools": tools, "task_type": "code"}
        else:
            payload = {"prompt": "\n\n".join("{}: {}".format(m.role, m.content) for m in msgs)}
        payload["complexity"] = tier
        payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode())
        tool_calls: List[ToolCall] = []
        for i, tc in enumerate(data.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args or "{}")
                except json.JSONDecodeError:
                    args = {}
            tool_calls.append(ToolCall(id=tc.get("id", "call_{}".format(i)),
                                       name=fn.get("name", ""), arguments=args))
        return Completion(text=data.get("text") or data.get("result") or "",
                          tool_calls=tool_calls, model="chuzom-http:" + tier,
                          prompt_tokens=data.get("input_tokens", 0),
                          completion_tokens=data.get("output_tokens", 0))

    def cost(self, c: Completion) -> float:
        return self._last.cost(c) if self._last else 0.0


def make_provider(spec: str) -> Provider:
    """spec is 'provider' or 'provider:model', e.g. 'ollama:llama3',
    'anthropic:claude-sonnet-4-6', 'chuzom:complex'."""
    if ":" in spec:
        name, model = spec.split(":", 1)
    else:
        name, model = spec, ""
    name = name.lower()
    if name == "ollama":
        return OllamaProvider(model=model or "llama3")
    if name == "anthropic":
        return AnthropicProvider(model=model or "claude-sonnet-4-6")
    if name == "openai":
        return OpenAIProvider(model=model or "gpt-4o")
    if name == "chuzom":
        return ChuzomProvider(tier=model or "auto")
    raise ProviderError("unknown provider: {}".format(name))


def detect_chuzom() -> bool:
    """True when Chuzom appears available to route through. loophole then defaults
    its swarm to ``chuzom:auto`` (cost-routed) instead of a fixed local model — so
    if Chuzom is running, loophole uses it automatically; if not, it falls back to
    local Ollama. Signals (any one): a live router endpoint, an explicit tier
    override, a local ``~/.chuzom`` install, or a ``chuzom`` binary on PATH."""
    import shutil
    if os.environ.get("CHUZOM_URL"):
        return True
    if os.environ.get("CHUZOM_TIER_COMPLEX") or os.environ.get("CHUZOM_TIER_MODERATE"):
        return True
    if os.path.isdir(os.path.expanduser("~/.chuzom")):
        return True
    return shutil.which("chuzom") is not None


def default_model() -> str:
    """The model spec loophole uses when the caller doesn't pick one: route through
    Chuzom when it's available, else a capable local Ollama coder ($0)."""
    return "chuzom:auto" if detect_chuzom() else "ollama:qwen3-coder:30b"
