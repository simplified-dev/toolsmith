"""Discover gradle modules under a root and cache the inventory.

Replaces hardcoded module maps. `toolsmith setup [--root DIR]` scans a directory
tree for gradle modules, computes each module's name / paths / base Java package /
short alias, and writes a cache the server and CLI read. This is what makes
toolsmith general-purpose: point it at any Java workspace and it self-configures.

Cache layout:
  <root>/.toolsmith/modules.json   - the discovered inventory (per workspace)
  <root>/.toolsmith/aliases.json   - optional {module-name: shorthand} overrides
  ~/.config/toolsmith/roots.json   - registry so the server finds a cache off-cwd
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

CACHE_DIRNAME = ".toolsmith"
CACHE_FILENAME = "modules.json"
OVERRIDES_FILENAME = "aliases.json"
REGISTRY = Path.home() / ".config" / "toolsmith" / "roots.json"

# Directories never worth walking into when scanning for modules.
_PRUNE = {
    "build", ".gradle", ".git", ".idea", ".intellijPlatform", "out", "target",
    "node_modules", "cache", "bin", "classes", "gradle", ".toolsmith", "generated",
}


def _base_package(module_dir: Path) -> str | None:
    """Base Java package = the single-child dir chain under src/main/java."""
    smj = module_dir / "src" / "main" / "java"
    if not smj.is_dir():
        return None
    cur, parts = smj, []
    while True:
        subs = [x for x in cur.iterdir() if x.is_dir()]
        jfiles = [x for x in cur.iterdir() if x.suffix == ".java"]
        if len(subs) == 1 and not jfiles:
            parts.append(subs[0].name)
            cur = subs[0]
        else:
            break
    return ".".join(parts) or None


def scan(root: str | os.PathLike) -> tuple[Path, list[dict]]:
    """Walks root for gradle modules. Returns (resolved_root, modules)."""
    root = Path(root).resolve()
    mods: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        if any(f.startswith("build.gradle") for f in filenames):
            d = Path(dirpath)
            rel = d.relative_to(root).as_posix()
            if rel == ".":
                continue  # the scan root itself (aggregator); not a module
            mods.append({
                "name": d.name,
                "path": rel,
                "package": _base_package(d),
                "buildable": (d / "src").is_dir(),
            })
    mods.sort(key=lambda m: m["path"])
    return root, mods


def _read_overrides(root: Path) -> dict[str, str]:
    f = root / CACHE_DIRNAME / OVERRIDES_FILENAME
    if f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def assign_shorthands(mods: list[dict], overrides: dict[str, str] | None = None) -> None:
    """Assigns a short alias to each module in place (collision-aware).

    Multi-word names use an acronym (asset-renderer -> ar); single-word names use
    the first three letters. Overrides ({name: shorthand}) win and are applied first.
    """
    overrides = overrides or {}
    taken: set[str] = set(overrides.values())
    for m in mods:
        m["shorthand"] = overrides.get(m["name"])
    for m in mods:
        if m["shorthand"]:
            continue
        words = [w for w in re.split(r"[-_]", m["name"]) if w]
        cand = ("".join(w[0] for w in words) if len(words) > 1 else m["name"][:3]).lower()
        base, n = cand, 2
        while cand in taken:
            cand = f"{base}{n}"
            n += 1
        m["shorthand"] = cand
        taken.add(cand)


def _register_root(root: Path) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    reg = {"default": None, "roots": []}
    if REGISTRY.exists():
        try:
            reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    rp = root.as_posix()
    reg.setdefault("roots", [])
    if rp not in reg["roots"]:
        reg["roots"].append(rp)
    reg["default"] = rp
    REGISTRY.write_text(json.dumps(reg, indent=2), encoding="utf-8")


def run_setup(root: str | os.PathLike = ".") -> tuple[Path, list[dict]]:
    """Scans root, assigns shorthands, writes the cache, and registers the root."""
    resolved, mods = scan(root)
    assign_shorthands(mods, _read_overrides(resolved))
    cache_dir = resolved / CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    (cache_dir / CACHE_FILENAME).write_text(
        json.dumps({"root": resolved.as_posix(), "modules": mods}, indent=2),
        encoding="utf-8",
    )
    _register_root(resolved)
    return resolved, mods


def resolve_root(explicit: str | os.PathLike | None = None) -> Path | None:
    """Finds the workspace root whose cache to use.

    Order: explicit arg -> TOOLSMITH_ROOT env -> cwd walk-up for a .toolsmith cache
    -> the registry default. Returns None if none is found.
    """
    def _has_cache(p: Path) -> bool:
        return (p / CACHE_DIRNAME / CACHE_FILENAME).is_file()

    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("TOOLSMITH_ROOT")
    if env and _has_cache(Path(env)):
        return Path(env).resolve()
    cur = Path.cwd().resolve()
    while True:
        if _has_cache(cur):
            return cur
        if cur == cur.parent:
            break
        cur = cur.parent
    if REGISTRY.exists():
        try:
            reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
            if reg.get("default") and _has_cache(Path(reg["default"])):
                return Path(reg["default"]).resolve()
        except (ValueError, OSError):
            pass
    return None


def load_inventory(explicit_root: str | os.PathLike | None = None) -> tuple[Path | None, list[dict]]:
    """Loads (root, modules) from the resolved cache, or (root|None, []) if absent."""
    root = resolve_root(explicit_root)
    if root is None:
        return None, []
    f = root / CACHE_DIRNAME / CACHE_FILENAME
    if not f.is_file():
        return root, []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return root, []
    return root, data.get("modules", [])
