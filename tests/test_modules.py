"""Tests for module resolution and the authoritative package-root map."""
from __future__ import annotations

import pytest

from toolsmith.modules import (
    ALIASES,
    PACKAGE_ROOTS,
    WORKSPACE,
    package_root,
)


def test_counter_intuitive_package_roots():
    # The roots that do NOT match the module directory name - the traps that
    # cause wrong-path guesses. Pinned so a careless edit can't silently break them.
    assert PACKAGE_ROOTS["collections"] == "dev.simplified.collection"  # singular
    assert PACKAGE_ROOTS["utils"] == "dev.simplified.util"              # singular
    assert PACKAGE_ROOTS["gson-extras"] == "dev.simplified.gson"
    assert PACKAGE_ROOTS["spring-framework"] == "dev.simplified.serverapi"
    assert PACKAGE_ROOTS["discord4j-framework"] == "dev.simplified.discordapi"


def test_package_root_resolves_through_alias():
    assert package_root("coll") == "dev.simplified.collection"
    assert package_root("spring") == "dev.simplified.serverapi"
    assert package_root("ar") == "lib.minecraft.renderer"


def test_every_alias_target_has_a_package_root_or_is_toolsmith():
    for rel in ALIASES.values():
        name = rel.rsplit("/", 1)[-1]
        assert name in PACKAGE_ROOTS or name == "toolsmith", name


@pytest.mark.skipif(not WORKSPACE.is_dir(), reason="workspace not present on this machine")
def test_all_aliases_point_to_real_dirs():
    missing = [rel for rel in ALIASES.values() if not (WORKSPACE / rel).is_dir()]
    assert missing == []
