"""Executor adapter SDK — register a named agent-framework backend so LoopHole can
run a swarm ON TOP OF it (Claude Code, Agno, Hermes, …).

An adapter is a factory ``(config: dict) -> Executor`` registered under a name. Core
resolves ``--executor <name>`` through this registry. Adapters are discovered via the
``loophole.executors`` entry-point group (so a pip package can ship one, like a
module), or registered in-process. Whatever the adapter runs, the trust boundary
(sandbox → write-allowlist → merge gate → verifier) is applied by core around it — no
adapter can grant 'done'.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .executor import Executor

# factory(config) -> Executor.  config carries CLI-supplied knobs (command, model,
# network hosts, secret keys, timeout) so the same adapter name works everywhere.
Factory = Callable[[Dict[str, Any]], Executor]

_ADAPTERS: Dict[str, Factory] = {}
_LOADED = False


class ExecutorAPI:
    """Handed to a package's ``register(api)`` so it never imports core internals."""
    def register_executor(self, name: str, factory: Factory) -> None:
        register_executor(name, factory)


def register_executor(name: str, factory: Factory) -> None:
    if name in ("react", "command"):
        raise ValueError("'{}' is a builtin executor".format(name))
    _ADAPTERS[name] = factory


def get_factory(name: str) -> Optional[Factory]:
    return _ADAPTERS.get(name)


def registered_executors() -> List[str]:
    return sorted(_ADAPTERS)


def resolve_executor(name: str, config: Optional[Dict[str, Any]] = None) -> Executor:
    """Build a registered adapter by name. Raises ValueError if unknown."""
    load_executors()
    factory = _ADAPTERS.get(name)
    if factory is None:
        raise ValueError(
            "unknown executor '{}'. Registered: {} (or use --executor-command / "
            "--executor-model)".format(name, ", ".join(registered_executors()) or "none"))
    return factory(config or {})


def load_executors() -> List[str]:
    """Discover installed adapters via the 'loophole.executors' entry-point group.
    Idempotent; a failing adapter never breaks the others."""
    global _LOADED
    if _LOADED:
        return registered_executors()
    _LOADED = True
    api = ExecutorAPI()
    try:                                    # built-in framework adapters (Phase 2)
        from . import adapters
        adapters.register(api)
    except Exception:
        pass
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        group = eps.select(group="loophole.executors") if hasattr(eps, "select") \
            else eps.get("loophole.executors", [])
        for ep in group:
            try:
                ep.load()(api)
            except Exception:
                pass
    except Exception:
        pass
    return registered_executors()
