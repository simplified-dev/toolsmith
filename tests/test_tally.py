"""Tests for the JUnit result tally."""
from __future__ import annotations

from toolsmith.tally import tally_dir

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
