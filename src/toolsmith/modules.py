"""Module resolution backed by the discovered inventory cache.

Nothing here is hardcoded to a particular workspace. `toolsmith setup` (see
discovery.py) scans a root and writes the cache; the lookups below read it. Run
setup once per workspace, or set TOOLSMITH_ROOT, so these resolve.
"""
from __future__ import annotations

import functools
from pathlib import Path

from . import discovery


@functools.lru_cache(maxsize=1)
def _inventory() -> tuple[Path | None, list[dict], dict[str, dict], dict[str, dict]]:
    root, mods = discovery.load_inventory()
    by_short = {m["shorthand"]: m for m in mods if m.get("shorthand")}
    by_name = {m["name"]: m for m in mods}
    return root, mods, by_short, by_name


def reload() -> None:
    """Drops the cached inventory so the next lookup re-reads the cache file."""
    _inventory.cache_clear()


def workspace_root() -> Path | None:
    """The active workspace root (from the resolved cache), or None if unconfigured."""
    return _inventory()[0]


def get_modules() -> list[dict]:
    """The full discovered module list (each: name, path, package, shorthand, buildable)."""
    return _inventory()[1]


def _lookup(token: str) -> dict | None:
    _root, _mods, by_short, by_name = _inventory()
    return by_short.get(token) or by_name.get(token)


def resolve_module(token: str) -> Path | None:
    """Resolves a shorthand, module name, or path to a directory.

    Args:
        token: module shorthand, name, or filesystem path.

    Returns:
        The resolved directory, or None when nothing matches (run `toolsmith setup`
        if a known module is not resolving).
    """
    root, _mods, _bs, _bn = _inventory()
    m = _lookup(token)
    if m and root is not None:
        return root / m["path"]
    path = Path(token)
    return path if path.is_dir() else None


def package_root(token: str) -> str | None:
    """Returns the base Java package for a module, or None if unknown/non-Java.

    Args:
        token: module shorthand, name, or path.

    Returns:
        The discovered base package (e.g. "dev.simplified.collection"), or None.
    """
    m = _lookup(token)
    return m["package"] if m else None


def kind_of(token: str) -> str | None:
    """Returns the build system recorded for a module, or None if it names none.

    None is not "no build system": it is also every bare filesystem path, which
    resolve_module accepts and the inventory has never heard of. So a caller
    refusing a wrong kind must refuse a KNOWN wrong one and let None through,
    or naming a module by path would stop working.

    Args:
        token: module shorthand, name, or path.

    Returns:
        "gradle", "maven", "python", or None when the token matches no module or
        the module declares no build system.
    """
    m = _lookup(token)
    return m.get("kind") if m else None


def is_repo(token: str) -> bool:
    """Whether a module is its own git repository root, by the cached scan."""
    m = _lookup(token)
    return bool(m and m.get("repo"))


def find_gradle_root(start: Path) -> Path | None:
    """Walks up from start to the nearest directory holding a gradle wrapper."""
    current = start.resolve()
    while True:
        if (current / "gradlew").exists() or (current / "gradlew.bat").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent
