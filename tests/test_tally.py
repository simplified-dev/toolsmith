"""Tests for the JUnit result tally."""
from __future__ import annotations

import os
import time

from toolsmith.tally import tally, tally_dir

SUITE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<testsuite name="com.x.WidgetTest" tests="4" skipped="1" failures="1" errors="1">
  <testcase classname="com.x.WidgetTest" name="passes"/>
  <testcase classname="com.x.WidgetTest" name="isSkipped"><skipped/></testcase>
  <testcase classname="com.x.WidgetTest" name="breaks"><failure>boom</failure></testcase>
  <testcase classname="com.x.WidgetTest" name="errors"><error>bang</error></testcase>
</testsuite>
"""


def _make_results(tmp_path):
    results = tmp_path / "build" / "test-results" / "test"
    results.mkdir(parents=True)
    (results / "TEST-com.x.WidgetTest.xml").write_text(SUITE_XML, encoding="utf-8")


def test_a_non_gradle_project_says_so_rather_than_reporting_no_tests(tmp_path, monkeypatch):
    """An absent JUnit directory reads as "no tests" and says nothing about why."""
    from toolsmith import modules

    (tmp_path / "toolsmith").mkdir()
    module = {"shorthand": "ts", "name": "toolsmith", "path": "toolsmith", "kind": "python",
              "repo": True, "package": None, "buildable": False}
    monkeypatch.setattr(modules, "_inventory",
                        lambda: (tmp_path, [module], {"ts": module}, {"toolsmith": module}))

    result = tally("toolsmith")

    assert result["found"] is False
    assert "python" in result["note"]


def test_counts_and_failing_names(tmp_path):
    _make_results(tmp_path)
    result = tally_dir(tmp_path)
    assert result["found"]
    assert result["tests"] == 4
    assert result["skipped"] == 1
    assert result["failures"] == 1
    assert result["errors"] == 1
    assert result["passed"] == 1
    assert result["ok"] is False
    assert "WidgetTest::breaks" in result["failing_tests"]
    assert "WidgetTest::errors" in result["failing_tests"]


def test_no_results_is_reported(tmp_path):
    result = tally_dir(tmp_path)
    assert result["found"] is False
    assert result["classes"] == 0


def test_green_when_no_failures(tmp_path):
    results = tmp_path / "build" / "test-results" / "test"
    results.mkdir(parents=True)
    (results / "TEST-ok.xml").write_text(
        '<testsuite name="p.OkTest" tests="2" skipped="0" failures="0" errors="0">'
        '<testcase classname="p.OkTest" name="a"/>'
        '<testcase classname="p.OkTest" name="b"/></testsuite>',
        encoding="utf-8",
    )
    result = tally_dir(tmp_path)
    assert result["ok"] is True
    assert result["passed"] == 2
    assert result["failing_tests"] == []


def _suite(results, name, tests, failures=0, failing=()):
    """Writes one JUnit suite file into a results directory."""
    results.mkdir(parents=True, exist_ok=True)
    cases = "".join(f'<testcase name="{c}"><failure>x</failure></testcase>' for c in failing)
    (results / f"TEST-{name}.xml").write_text(
        f'<testsuite name="p.{name}" tests="{tests}" skipped="0" failures="{failures}" '
        f'errors="0">{cases}</testsuite>',
        encoding="utf-8",
    )


def _multi_project(tmp_path):
    """Lays out the annotations shape: no root results, one subproject with two tasks."""
    _suite(tmp_path / "library" / "build" / "test-results" / "test", "UnitTest", 3)
    _suite(tmp_path / "library" / "build" / "test-results" / "aptTest", "AptTest", 5,
           failures=1, failing=["rejects"])
    _suite(tmp_path / "plugin" / "build" / "test-results" / "test", "FixtureTest", 7)


def test_a_multi_project_build_tallies_every_subproject_and_task(tmp_path):
    """The results of a multi-project build sit under each subproject, none at the root."""
    _multi_project(tmp_path)

    result = tally_dir(tmp_path)

    assert result["found"] is True
    assert result["classes"] == 3
    assert result["tests"] == 15
    assert result["failures"] == 1
    assert result["passed"] == 14
    assert result["ok"] is False
    assert result["failing_tests"] == ["AptTest::rejects"]


def test_the_breakdown_names_each_results_directory_by_path_and_task(tmp_path):
    _multi_project(tmp_path)
    _suite(tmp_path / "build" / "test-results" / "test", "RootTest", 2)

    rows = [(r["path"], r["task"], r["tests"], r["failures"])
            for r in tally_dir(tmp_path)["results"]]

    assert rows == [(".", "test", 2, 0),
                    ("library", "aptTest", 5, 1),
                    ("library", "test", 3, 0),
                    ("plugin", "test", 7, 0)]


def test_a_nested_subproject_is_found_at_any_depth(tmp_path):
    _suite(tmp_path / "libs" / "core" / "build" / "test-results" / "integrationTest", "ItTest", 4)

    result = tally_dir(tmp_path)

    assert result["tests"] == 4
    assert [(r["path"], r["task"]) for r in result["results"]] == [("libs/core", "integrationTest")]


def test_tool_and_build_directories_are_not_searched_for_subprojects(tmp_path):
    """A build directory's contents and tool state are not subprojects, whatever they hold."""
    for skipped in (".git", ".gradle", "node_modules", "build/tmp"):
        _suite(tmp_path / skipped / "build" / "test-results" / "test", "StrayTest", 9)
    _suite(tmp_path / "build" / "test-results" / "test", "RootTest", 2)

    result = tally_dir(tmp_path)

    assert result["tests"] == 2
    assert [r["path"] for r in result["results"]] == ["."]


def test_a_single_results_directory_returns_one_row_and_prints_no_breakdown(
        tmp_path, capsys, monkeypatch):
    """A module holding only build/test-results/test keeps its one-line report.

    Its result still names that one directory, so a caller reading ``results``
    sees the same shape whether a build has one results directory or several.
    """
    from toolsmith import tally as tally_mod

    _make_results(tmp_path)
    monkeypatch.setattr(tally_mod, "resolve_module", lambda m: tmp_path)
    monkeypatch.setattr(tally_mod, "kind_of", lambda m: "gradle")

    result = tally_dir(tmp_path)
    assert [(r["path"], r["task"], r["classes"], r["tests"], r["passed"])
            for r in result["results"]] == [(".", "test", 1, 4, 1)]

    tally_mod._main(["mod"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("classes=1 tests=4 passed=1 skipped=1 failures=1 errors=1")
    assert [ln for ln in lines if not ln.startswith(("classes=", "FAIL "))] == []


def test_the_cli_prints_a_line_per_results_directory_when_there_are_several(
        tmp_path, capsys, monkeypatch):
    import argparse

    from toolsmith import cli
    from toolsmith import tally as tally_mod

    _multi_project(tmp_path)
    monkeypatch.setattr(tally_mod, "resolve_module", lambda m: tmp_path)
    monkeypatch.setattr(tally_mod, "kind_of", lambda m: "gradle")

    rc = cli._cmd_tally(argparse.Namespace(module="mod", fails=15))

    out = capsys.readouterr().out
    assert rc == 1
    assert out.splitlines()[0].startswith("classes=3 tests=15 passed=14")
    assert "library/aptTest" in out
    assert "library/test" in out
    assert "plugin/test" in out


_DAY = 24 * 60 * 60


def _touch(directory, when):
    """Sets the modification time of every file under a directory."""
    for path in directory.rglob("*"):
        if path.is_file():
            os.utime(path, (when, when))


def _compiled(project, source_set, when, language="java"):
    """Writes one compiled class into build/classes/<language>/<source_set>."""
    classes = project / "build" / "classes" / language / source_set
    (classes / "p").mkdir(parents=True, exist_ok=True)
    (classes / "p" / f"{source_set}Probe.class").write_bytes(b"\xca\xfe\xba\xbe")
    _touch(classes, when)


def _stale_rerun(tmp_path):
    """Lays out a library whose aptTest17 results predate the aptTest classes it ran.

    aptTest17 runs aptTest's classes, so it has no class directory of its own
    and is judged by the newest test class anywhere under the subproject.
    """
    now = time.time()
    library = tmp_path / "library"
    _suite(library / "build" / "test-results" / "test", "UnitTest", 3)
    _suite(library / "build" / "test-results" / "aptTest", "AptTest", 5)
    _suite(library / "build" / "test-results" / "aptTest17", "AptTest", 4,
           failures=1, failing=["oldBreak"])
    _touch(library / "build" / "test-results" / "test", now - 60)
    _touch(library / "build" / "test-results" / "aptTest", now - 60)
    _touch(library / "build" / "test-results" / "aptTest17", now - 3 * _DAY)
    _compiled(library, "test", now - 120)
    _compiled(library, "aptTest", now - 120)


def test_a_results_directory_older_than_its_compiled_tests_is_listed_stale_and_left_out(tmp_path):
    """A results directory an earlier run left behind must not reach the total."""
    _stale_rerun(tmp_path)

    result = tally_dir(tmp_path)

    rows = {r["task"]: r for r in result["results"]}
    assert set(rows) == {"aptTest", "aptTest17", "test"}
    assert rows["aptTest17"]["stale"] is True
    assert rows["aptTest"]["stale"] is False
    assert rows["test"]["stale"] is False
    assert rows["aptTest17"]["tests"] == 4
    assert result["tests"] == 8
    assert result["classes"] == 2
    assert result["failures"] == 0
    assert result["ok"] is True
    assert result["failing_tests"] == []
    assert result["failing_total"] == 0


def test_every_row_carries_the_age_of_its_newest_result(tmp_path):
    _stale_rerun(tmp_path)

    rows = {r["task"]: r for r in tally_dir(tmp_path)["results"]}

    assert 3 * _DAY - 60 <= rows["aptTest17"]["age_seconds"] <= 3 * _DAY + 60
    assert 0 <= rows["aptTest"]["age_seconds"] <= 120


def test_a_task_with_classes_of_its_own_name_is_judged_by_those_alone(tmp_path):
    """Newer classes of another source set do not make a results directory stale."""
    now = time.time()
    library = tmp_path / "library"
    _suite(library / "build" / "test-results" / "test", "UnitTest", 3)
    _suite(library / "build" / "test-results" / "aptTest", "AptTest", 5)
    _touch(library / "build" / "test-results" / "test", now - 2 * _DAY)
    _touch(library / "build" / "test-results" / "aptTest", now - 2 * _DAY)
    _compiled(library, "test", now - 3 * _DAY)
    _compiled(library, "aptTest", now - _DAY, language="kotlin")

    rows = {r["task"]: r for r in tally_dir(tmp_path)["results"]}

    assert rows["test"]["stale"] is False
    assert rows["aptTest"]["stale"] is True


def test_production_classes_do_not_make_test_results_stale(tmp_path):
    """The fallback compares against compiled TEST classes, and main is not one."""
    now = time.time()
    library = tmp_path / "library"
    _suite(library / "build" / "test-results" / "aptTest17", "AptTest", 4)
    _touch(library / "build" / "test-results" / "aptTest17", now - 2 * _DAY)
    _compiled(library, "main", now - 60)

    result = tally_dir(tmp_path)

    assert result["results"][0]["stale"] is False
    assert result["tests"] == 4


def test_a_results_directory_with_no_compiled_classes_is_never_stale(tmp_path):
    now = time.time()
    _suite(tmp_path / "build" / "test-results" / "test", "OldTest", 6)
    _touch(tmp_path / "build" / "test-results" / "test", now - 30 * _DAY)

    result = tally_dir(tmp_path)

    assert result["results"][0]["stale"] is False
    assert result["tests"] == 6


def test_the_cli_marks_a_stale_row_with_its_age(tmp_path, capsys, monkeypatch):
    import argparse

    from toolsmith import cli
    from toolsmith import tally as tally_mod

    _stale_rerun(tmp_path)
    monkeypatch.setattr(tally_mod, "resolve_module", lambda m: tmp_path)
    monkeypatch.setattr(tally_mod, "kind_of", lambda m: "gradle")

    rc = cli._cmd_tally(argparse.Namespace(module="mod", fails=15))

    lines = capsys.readouterr().out.splitlines()
    assert rc == 0
    assert lines[0].startswith("classes=2 tests=8 passed=8")
    stale = [ln for ln in lines if "library/aptTest17" in ln]
    assert len(stale) == 1 and "stale" in stale[0] and "3d" in stale[0]
    assert not any("stale" in ln for ln in lines if "library/aptTest17" not in ln)
    assert not any(ln.startswith("FAIL ") for ln in lines)


def test_a_lone_stale_directory_still_prints_its_row(tmp_path, capsys, monkeypatch):
    """A zero total with no row beside it would not say where the results went."""
    from toolsmith import tally as tally_mod

    now = time.time()
    _suite(tmp_path / "build" / "test-results" / "test", "OldTest", 6)
    _touch(tmp_path / "build" / "test-results" / "test", now - _DAY)
    _compiled(tmp_path, "test", now - 60)
    monkeypatch.setattr(tally_mod, "resolve_module", lambda m: tmp_path)
    monkeypatch.setattr(tally_mod, "kind_of", lambda m: "gradle")

    tally_mod._main(["mod"])

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("classes=0 tests=0 passed=0")
    assert any("./test" in ln and "stale" in ln for ln in lines[1:])
