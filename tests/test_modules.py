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


def _make_project(root, rel, marker: str, *, git: bool = False):
    """A directory declaring one build system, optionally a repository root."""
    d = root / rel
    d.mkdir(parents=True)
    (d / marker).write_text("", encoding="utf-8")
    if git:
        (d / ".git").mkdir()
    return d


def test_scan_records_each_build_system(tmp_path):
    _make_project(tmp_path, "a/grad", "build.gradle")
    _make_project(tmp_path, "a/kts", "build.gradle.kts")
    _make_project(tmp_path, "a/mvn", "pom.xml")
    _make_project(tmp_path, "a/py", "pyproject.toml")
    _make_project(tmp_path, "a/legacy-py", "setup.py")

    _root, mods = discovery.scan(tmp_path)

    kinds = {m["name"]: m["kind"] for m in mods}
    assert kinds == {"grad": "gradle", "kts": "gradle", "mvn": "maven",
                     "py": "python", "legacy-py": "python"}


def test_a_directory_declaring_two_build_systems_is_the_first_one(tmp_path):
    """A gradle project holding a pyproject.toml for its dev scripts is a gradle project."""
    d = _make_project(tmp_path, "both", "build.gradle.kts")
    (d / "pyproject.toml").write_text("", encoding="utf-8")

    _root, mods = discovery.scan(tmp_path)

    assert [m["kind"] for m in mods] == ["gradle"]


def test_a_repository_with_no_build_file_is_still_discovered(tmp_path):
    """branch finish works on repositories, so a repository is a row on its own."""
    _make_project(tmp_path, "docs-only", "README.md", git=True)

    _root, mods = discovery.scan(tmp_path)

    assert len(mods) == 1
    assert mods[0]["name"] == "docs-only"
    assert mods[0]["kind"] is None
    assert mods[0]["repo"] is True


def test_kind_and_repo_vary_independently(tmp_path):
    """A module can sit in a repository it does not own, and a repository can carry no build."""
    _make_project(tmp_path, "repo", "build.gradle.kts", git=True)
    _make_project(tmp_path, "repo/nested", "build.gradle.kts")

    _root, mods = discovery.scan(tmp_path)

    by_name = {m["name"]: m for m in mods}
    assert by_name["repo"]["repo"] is True
    assert by_name["nested"]["repo"] is False
    assert by_name["nested"]["kind"] == "gradle"


def test_a_directory_declaring_nothing_is_no_row(tmp_path):
    (tmp_path / "just-a-folder").mkdir()

    _root, mods = discovery.scan(tmp_path)

    assert mods == []


def test_buildable_means_a_jvm_source_tree_not_merely_a_src_dir(tmp_path):
    """A python project has a src/ too, and java locate would search it for .java."""
    d = _make_project(tmp_path, "py", "pyproject.toml")
    (d / "src" / "package").mkdir(parents=True)
    _make_module(tmp_path, "jvm", "dev/acme/thing")

    _root, mods = discovery.scan(tmp_path)

    by_name = {m["name"]: m for m in mods}
    assert by_name["py"]["buildable"] is False
    assert by_name["jvm"]["buildable"] is True


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
