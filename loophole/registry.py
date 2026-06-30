"""Verifier/contract registry — shareable, named acceptance specs.

A contract reference resolves by NAME from (1) the user registry
(``$LOOPHOLE_REGISTRY_DIR`` or ``~/.loophole/registry``) then (2) the bundled
templates — so a team can ``registry add`` a spec (from a path or URL) once and
everyone runs it by name. The network-effect seed for "acceptance-spec-as-code":
contracts become reusable, shareable artifacts instead of one-off files.
"""

from __future__ import annotations

import os
from typing import List

from .contract import GoalContract
from .initializer import list_templates, load_template_raw, load_contract

_URL_PREFIXES = ("http://", "https://", "file://")


def registry_dir() -> str:
    d = os.environ.get("LOOPHOLE_REGISTRY_DIR")
    return d if d else os.path.join(os.path.expanduser("~"), ".loophole", "registry")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_raw(src: str) -> str:
    """Raw contract text from a local path or an http(s)/file URL."""
    if src.startswith(_URL_PREFIXES):
        import urllib.request
        with urllib.request.urlopen(src, timeout=15) as r:  # nosec - operator-supplied
            return r.read().decode("utf-8")
    return _read(src)


def list_entries() -> List[dict]:
    """All resolvable entries: bundled templates + the user registry."""
    out = [{"name": n, "source": "bundled"} for n in list_templates()]
    d = registry_dir()
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json"):
                out.append({"name": f[:-5], "source": "local"})
    return out


def resolve(name: str) -> str:
    """Raw JSON for a registry NAME — user registry wins over a bundled template."""
    local = os.path.join(registry_dir(), name + ".json")
    if os.path.exists(local):
        return _read(local)
    try:
        return load_template_raw(name)
    except ValueError:
        raise ValueError(
            "unknown registry entry '{}' — see `loophole registry list`".format(name))


def get_contract(name: str) -> GoalContract:
    return GoalContract.from_json(resolve(name))


def add(name: str, src: str) -> str:
    """Pull a contract from a path/URL into the local registry under ``name``."""
    raw = _load_raw(src)
    GoalContract.from_json(raw)          # validate it parses before saving
    d = registry_dir()
    os.makedirs(d, exist_ok=True)
    dest = os.path.join(d, name + ".json")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(raw if raw.endswith("\n") else raw + "\n")
    return dest


def remove(name: str) -> bool:
    """Delete a LOCAL registry entry (bundled templates can't be removed)."""
    p = os.path.join(registry_dir(), name + ".json")
    if os.path.exists(p):
        os.remove(p)
        return True
    return False


def load_ref(ref: str) -> GoalContract:
    """Resolve a contract reference: a URL / existing file path, else a registry NAME.
    Lets `run --contract` and `init --template` accept files, URLs, or names alike."""
    if ref.startswith(_URL_PREFIXES) or os.path.exists(ref):
        return load_contract(ref)
    return get_contract(ref)
