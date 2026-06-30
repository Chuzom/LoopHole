"""Verifier/contract registry — shareable, named acceptance specs.

A contract reference resolves by NAME from (1) the user registry
(``$LOOPHOLE_REGISTRY_DIR`` or ``~/.loophole/registry``) then (2) the bundled
templates — so a team can ``registry add`` a spec (from a path or URL) once and
everyone runs it by name. The network-effect seed for "acceptance-spec-as-code":
contracts become reusable, shareable artifacts instead of one-off files.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

from .contract import GoalContract
from .initializer import list_templates, load_template_raw, load_contract

_URL_PREFIXES = ("http://", "https://", "file://")
_SOURCES_FILE = "_sources.json"


def registry_dir() -> str:
    d = os.environ.get("LOOPHOLE_REGISTRY_DIR")
    return d if d else os.path.join(os.path.expanduser("~"), ".loophole", "registry")


# ── remote index sources (a shared registry served over a URL) ──────────────
def _sources_path() -> str:
    return os.path.join(registry_dir(), _SOURCES_FILE)


def list_sources() -> List[str]:
    p = _sources_path()
    if not os.path.exists(p):
        return []
    try:
        data = json.loads(_read(p))
        return [str(u) for u in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_sources(urls: List[str]) -> None:
    os.makedirs(registry_dir(), exist_ok=True)
    with open(_sources_path(), "w", encoding="utf-8") as f:
        f.write(json.dumps(urls, indent=2) + "\n")


def add_source(url: str) -> None:
    urls = list_sources()
    if url not in urls:
        urls.append(url)
        _save_sources(urls)


def remove_source(url: str) -> bool:
    urls = list_sources()
    if url in urls:
        urls.remove(url)
        _save_sources(urls)
        return True
    return False


def _fetch(url: str) -> Optional[str]:
    """Fetch raw text from an http(s)/file URL; None on any failure (offline-safe)."""
    try:
        if url.startswith(_URL_PREFIXES):
            import urllib.request
            with urllib.request.urlopen(url, timeout=15) as r:  # nosec - operator-supplied
                return r.read().decode("utf-8")
        return _read(url)
    except Exception:
        return None


def remote_entries() -> Dict[str, dict]:
    """name -> {"url": <contract-url>} or {"inline": <raw json>}, merged across the
    configured index sources. An index is a JSON manifest mapping name -> a contract
    URL (resolved relative to the index) or an inline contract object."""
    from urllib.parse import urljoin
    out: Dict[str, dict] = {}
    for src in list_sources():
        txt = _fetch(src)
        if not txt:
            continue
        try:
            idx = json.loads(txt)
        except json.JSONDecodeError:
            continue
        entries = idx.get("entries", idx) if isinstance(idx, dict) else None
        if not isinstance(entries, dict):
            continue
        for name, val in entries.items():
            if isinstance(val, str):
                out[name] = {"url": urljoin(src, val), "source": src}
            elif isinstance(val, dict):
                out[name] = {"inline": json.dumps(val), "source": src}
    return out


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


def list_entries(include_remote: bool = True) -> List[dict]:
    """All resolvable entries: bundled templates + local registry + remote index."""
    out = [{"name": n, "source": "bundled"} for n in list_templates()]
    d = registry_dir()
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".json") and f != _SOURCES_FILE:
                out.append({"name": f[:-5], "source": "local"})
    if include_remote:
        for name in sorted(remote_entries()):
            out.append({"name": name, "source": "remote"})
    return out


def resolve(name: str) -> str:
    """Raw JSON for a registry NAME. Precedence: local user registry > remote index
    > bundled template."""
    local = os.path.join(registry_dir(), name + ".json")
    if os.path.exists(local):
        return _read(local)
    rem = remote_entries().get(name)
    if rem is not None:
        if "inline" in rem:
            return rem["inline"]
        raw = _fetch(rem["url"])
        if raw:
            return raw
        raise ValueError("registry entry '{}' is unreachable ({})".format(name, rem.get("url")))
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
