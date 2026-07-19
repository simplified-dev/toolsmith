"""Tests for module discovery and cache-backed resolution."""
from __future__ import annotations

from toolsmith import discovery, modules


def _make_module(root, rel, pkg_path):
    d = root / rel
    d.mkdir(parents=True)
    (d / "build.gradle.kts").write_text("", encoding="utf-8")
    smj = d / "src" / "main" / "java"
    (smj / pkg_path).mkdir(parents=True)
    (smj / pkg_path / "X.java").write_text(
        "package " + pkg_path.replace("/", ".") + ";\n", encoding="utf-8")


def test_scan_finds_modules_and_base_packages(tmp_path):
    _make_module(tmp_path, "libs/collections", "dev/acme/collection")  # singular pkg leaf
    _make_module(tmp_path, "libs/utils", "dev/acme/util")
    root, mods = discovery.scan(tmp_path)
    by_name = {m["name"]: m for m in mods}
    assert by_name["collections"]["package"] == "dev.acme.collection"
    assert by_name["utils"]["package"] == "dev.acme.util"
    assert by_name["collections"]["path"] == "libs/collections"
    assert all(m["buildable"] for m in mods)


def test_assign_shorthands_unique_and_override():
    mods = [{"name": n} for n in ("asset-renderer", "minecraft-text", "reflection", "records")]
    discovery.assign_shorthands(mods, overrides={"reflection": "refl"})
    sh = {m["name"]: m["shorthand"] for m in mods}
    assert sh["asset-renderer"] == "ar"   # acronym of hyphen words
    assert sh["minecraft-text"] == "mt"
    assert sh["reflection"] == "refl"      # override honored
    assert len(set(sh.values())) == len(sh)  # all unique


def test_setup_load_and_resolve_roundtrip(tmp_path, monkeypatch):
    _make_module(tmp_path, "m/collections", "dev/acme/collection")
    monkeypatch.setattr(discovery, "REGISTRY", tmp_path / "reg.json")
    (tmp_path / ".toolsmith").mkdir()
    (tmp_path / ".toolsmith" / "aliases.json").write_text('{"collections": "coll"}', encoding="utf-8")

    root, mods = discovery.run_setup(tmp_path)
    assert (tmp_path / ".toolsmith" / "modules.json").is_file()
    assert (tmp_path / "reg.json").is_file()  # root registered

    monkeypatch.setenv("TOOLSMITH_ROOT", str(tmp_path))
    modules.reload()
    try:
        assert modules.package_root("collections") == "dev.acme.collection"
        assert modules.package_root("coll") == "dev.acme.collection"  # override shorthand
        assert modules.resolve_module("coll").resolve() == (tmp_path / "m" / "collections").resolve()
    finally:
        modules.reload()  # don't leak the tmp inventory into other tests
