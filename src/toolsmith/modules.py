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

# Short aliases -> workspace-relative module path. Complete for every buildable
# module (34 total). A bare module name that is not an alias still resolves via
# resolve_module's directory-name search, so this table is a convenience, not the
# only path in.
ALIASES: dict[str, str] = {
    # Minecraft-Library
    "ar": "Minecraft-Library/asset-renderer",
    "mt": "Minecraft-Library/minecraft-text",
    "nbt": "Minecraft-Library/nbt-factory",
    "vrh": "Minecraft-Library/vanilla-reference-harness",
    # Simplified-Api
    "github": "Simplified-Api/github",
    "hypixel": "Simplified-Api/hypixel",
    "mojang": "Simplified-Api/mojang",
    "skyblockdata": "Simplified-Api/skyblock",
    # Simplified-Dev
    "annotations": "Simplified-Dev/annotations",
    "client": "Simplified-Dev/client",
    "coll": "Simplified-Dev/collections",
    "dataflow": "Simplified-Dev/dataflow",
    "d4j": "Simplified-Dev/discord4j-framework",
    "expression": "Simplified-Dev/expression",
    "gson": "Simplified-Dev/gson-extras",
    "image": "Simplified-Dev/image",
    "manager": "Simplified-Dev/manager",
    "pers": "Simplified-Dev/persistence",
    "refl": "Simplified-Dev/reflection",
    "scheduler": "Simplified-Dev/scheduler",
    "spring": "Simplified-Dev/spring-framework",
    "utils": "Simplified-Dev/utils",
    "yaml": "Simplified-Dev/yaml",
    "toolsmith": "Simplified-Dev/toolsmith",
    # SkyBlock-Simplified
    "sbsapi": "SkyBlock-Simplified/sbs-api",
    "bot": "SkyBlock-Simplified/simplified-bot",
    "data": "SkyBlock-Simplified/simplified-data",
    "srv": "SkyBlock-Simplified/simplified-server",
}

# Authoritative module-dir-name -> base Java package. This is the "do not guess"
# table: several roots are counter-intuitive (singular collection/util, gson,
# and spring-framework -> serverapi), which is exactly where path guesses fail.
# Verified by scanning src/main/java of every module.
PACKAGE_ROOTS: dict[str, str] = {
    "asset-renderer": "lib.minecraft.renderer",
    "minecraft-text": "lib.minecraft.text",
    "nbt-factory": "lib.minecraft.nbt",
    "vanilla-reference-harness": "lib.minecraft.refharness",
    "github": "api.simplified.github",
    "hypixel": "api.simplified.hypixel",
    "mojang": "api.simplified.mojang",
    "skyblock": "dev.sbs.skyblockdata",
    "annotations": "dev.simplified",  # library/ and plugin/ subprojects
    "client": "dev.simplified.client",
    "collections": "dev.simplified.collection",   # NOTE: singular
    "dataflow": "dev.simplified.dataflow",
    "discord4j-framework": "dev.simplified.discordapi",
    "expression": "dev.simplified.expression",
    "gson-extras": "dev.simplified.gson",
    "image": "dev.simplified.image",
    "manager": "dev.simplified.manager",
    "persistence": "dev.simplified.persistence",
    "reflection": "dev.simplified.reflection",
    "scheduler": "dev.simplified.scheduler",
    "spring-framework": "dev.simplified.serverapi",   # NOTE: not 'spring'
    "utils": "dev.simplified.util",   # NOTE: singular
    "yaml": "dev.simplified.yaml",
    "sbs-api": "dev.sbs.sbsapi",
    "simplified-bot": "dev.sbs.simplifiedbot",
    "simplified-data": "dev.sbs.simplifieddata",
    "simplified-server": "dev.sbs.simplifiedserver",
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


def package_root(module: str) -> str | None:
    """Returns the base Java package for a module, or None if unknown/non-Java.

    Args:
        module: module alias, name, or path.

    Returns:
        The verified base package (e.g. "dev.simplified.collection"), or None
        when the module is not a known Java module (e.g. toolsmith).
    """
    resolved = resolve_module(module)
    return PACKAGE_ROOTS.get(resolved.name) if resolved else None
