"""Module resolution for the Simplified Java workspace.

The four family roots live directly under ``SIMPLIFIED_ROOT``. A caller may name
a module by a short alias, by a bare directory name (searched for), or by an
explicit path. Everything else in the package resolves modules through here so
there is a single source of truth for where things live.
"""
from __future__ import annotations

import os
from pathlib import Path

# Workspace root - override with SIMPLIFIED_ROOT for a different checkout.
WORKSPACE = Path(os.environ.get("SIMPLIFIED_ROOT", "W:/Workspace/Java/Simplified"))

# Short aliases for the common modules (mirrors what a human types).
ALIASES: dict[str, str] = {
    "ar": "Minecraft-Library/asset-renderer",
    "mt": "Minecraft-Library/minecraft-text",
    "nbt": "Minecraft-Library/nbt-factory",
    "vrh": "Minecraft-Library/vanilla-reference-harness",
    "d4j": "Simplified-Dev/discord4j-framework",
    "spring": "Simplified-Dev/spring-framework",
    "pers": "Simplified-Dev/persistence",
    "gson": "Simplified-Dev/gson-extras",
    "coll": "Simplified-Dev/collections",
    "refl": "Simplified-Dev/reflection",
    "ann": "Simplified-Dev/annotations",
    "image": "Simplified-Dev/image",
    "toolsmith": "Simplified-Dev/toolsmith",
    "mojang": "Simplified-Api/mojang",
    "hypixel": "Simplified-Api/hypixel",
    "bot": "SkyBlock-Simplified/simplified-bot",
    "srv": "SkyBlock-Simplified/simplified-server",
}

# Directories never worth walking into when searching for a module by name.
_PRUNE = {
    "build", ".git", ".gradle", ".idea", ".intellijPlatform", "out", "target",
    "node_modules", "cache", "bin", "classes",
}


def _search_by_name(name: str) -> Path | None:
    """Finds a gradle module directory by its bare name, pruning heavy dirs."""
    for dirpath, dirnames, filenames in os.walk(WORKSPACE):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE]
        if os.path.basename(dirpath) == name and any(
            f.startswith("build.gradle") for f in filenames
        ):
            return Path(dirpath)
    return None


def resolve_module(token: str) -> Path | None:
    """Resolves an alias, explicit path, or bare module name to a directory.

    Args:
        token: module alias, filesystem path, or gradle module directory name.

    Returns:
        The resolved directory, or None when nothing matches.
    """
    if token in ALIASES:
        candidate = WORKSPACE / ALIASES[token]
        return candidate if candidate.exists() else None
    path = Path(token)
    if path.is_dir():
        return path
    return _search_by_name(token)


def find_gradle_root(start: Path) -> Path | None:
    """Walks up from start to the nearest directory holding a gradle wrapper."""
    current = start.resolve()
    while True:
        if (current / "gradlew").exists() or (current / "gradlew.bat").exists():
            return current
        if current == current.parent:
            return None
        current = current.parent
