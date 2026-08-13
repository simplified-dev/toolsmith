"""Discover projects under a root and cache the inventory.

Replaces hardcoded module maps. `toolsmith setup [--root DIR]` scans a directory
tree for projects, computes each one's name / paths / base Java package / short
alias, and writes a cache the server and CLI read. This is what makes toolsmith
general-purpose: point it at any workspace and it self-configures.

Two INDEPENDENT axes are recorded, because commands divide on both:

  * ``kind`` - which build system applies, from one marker file each. A command
    that shells a build reads this: `gradle verify` against a python project
    would otherwise walk UP for a wrapper, find a SIBLING project's gradlew and
    run that build rather than refusing.
  * ``repo`` - whether the directory is a git repository root. Repository-level
    commands read this instead, and the two genuinely differ in both directions:
    a gradle module can sit inside a repo it does not own, and a repository can
    carry no build file at all and still be perfectly finishable.

Cache layout:
  <root>/.toolsmith/modules.json   - the discovered inventory (per workspace)
  <root>/.toolsmith/aliases.json   - optional {path-or-name: shorthand} overrides
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
    "venv", ".venv", "__pycache__", ".tox", "dist", ".eggs",
}

# One marker file per kind, tested in order, so a directory carrying two answers
# to the first. A gradle project holding a pyproject.toml for its dev scripts is
# a gradle project, and the reverse ordering would rename it.
_KIND_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gradle", ("build.gradle", "build.gradle.kts")),
    ("maven", ("pom.xml",)),
    ("python", ("pyproject.toml", "setup.py", "setup.cfg")),
)

# Kinds laying their sources out as src/main/java. Maven shares gradle's layout,
# which is why base-package discovery serves both and neither names it.
_JVM_KINDS = ("gradle", "maven")


def _kind_of(filenames: list[str]) -> str | None:
    """The project kind a directory's own files declare, or None for neither."""
    names = set(filenames)
    for kind, markers in _KIND_MARKERS:
        if names.intersection(markers):
            return kind
    return None


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
    """Walks root for projects and repositories. Returns (resolved_root, modules).

    A directory earns a row by declaring a build system, by being a git
    repository root, or by both - so a repository carrying no build file is
    still nameable, and a module nested in a repository it does not own is
    still its own row.

    Args:
        root: directory to walk.

    Returns:
        (resolved_root, modules), each module carrying name, path, kind, repo,
        package and buildable, sorted by path.
    """
    root = Path(root).resolve()
    mods: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        d = Path(dirpath)
        rel = d.relative_to(root).as_posix()
        if rel == ".":
            continue  # the scan root itself (aggregator); not a module
        kind = _kind_of(filenames)
        # .git is a FILE in a worktree and a directory in a normal clone, so
        # existence is the test rather than is_dir.
        repo = (d / ".git").exists()
        if kind is None and not repo:
            continue
        mods.append({
            "name": d.name,
            "path": rel,
            "kind": kind,
            "repo": repo,
            # src/main/java answers for itself: a python project has none, so
            # this needs no guard of its own.
            "package": _base_package(d),
            # A JVM source tree, not merely a src/ directory - a python project
            # has one of those too, and java locate would search it for .java.
            "buildable": kind in _JVM_KINDS and (d / "src").is_dir(),
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


def _generated(name: str) -> str:
    """The alias a name earns on its own: an acronym, or its first three letters."""
    words = [w for w in re.split(r"[-_]", name) if w]
    return ("".join(w[0] for w in words) if len(words) > 1 else name[:3]).lower()


def _claim(candidate: str, taken: set[str]) -> str:
    """Returns candidate, or the first free numeric variant of it, and claims it."""
    cand, base, n = candidate, candidate, 2
    while cand in taken:
        cand = f"{base}{n}"
        n += 1
    taken.add(cand)
    return cand


def assign_shorthands(mods: list[dict], overrides: dict[str, str] | None = None) -> None:
    """Assigns a short alias to each module in place (collision-aware).

    Multi-word names use an acronym (asset-renderer -> ar); single-word names use
    the first three letters.

    An override may be keyed by workspace-relative PATH or by name, and a path
    key wins, because a NAME IS NOT UNIQUE: two directories both called "client"
    take one name-keyed alias between them, and no name-keyed entry can tell them
    apart. A path names exactly one module and always can.

    Every alias goes through the same collision check, an override included: an
    override states a preference, not a proof of uniqueness, and two modules
    claiming one alias leave the second unreachable if the second is not moved
    aside. Overrides are claimed first so an explicit preference still beats a
    generated candidate that happens to collide with it, and mods arrive sorted
    by path, so which of two equal claims is moved aside is stable.

    Args:
        mods: module dicts, mutated in place to carry "shorthand".
        overrides: {path-or-name: shorthand}, path winning over name.
    """
    overrides = overrides or {}
    for m in mods:
        m["shorthand"] = overrides.get(m.get("path")) or overrides.get(m["name"])

    taken: set[str] = set()
    for m in mods:
        if m["shorthand"]:
            m["shorthand"] = _claim(m["shorthand"], taken)
    for m in mods:
        if not m["shorthand"]:
            m["shorthand"] = _claim(_generated(m["name"]), taken)


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
