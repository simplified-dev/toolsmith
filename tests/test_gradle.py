"""Tests for the module-scoped gradle gate.

These drive a stand-in gradle wrapper rather than a real build, so the actual
subprocess plumbing is exercised - file sink, exit-code capture, timeout
backstop, signal extraction - without needing Gradle or a JDK.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

from toolsmith import cli, server
from toolsmith.gradle import _windows_command_line, gradle_verify

ARGV_FILE = "argv.txt"

# A wrapper that emits canned output and exits with a chosen code. The payload
# is Python rather than shell so the canned text can hold any character -
# batch would need escaping for the '>' in gradle's own "> Task ..." lines.
_PAYLOAD = """\
import sys, time
from pathlib import Path
Path({argv!r}).write_text("\\n".join(sys.argv[1:]), encoding="utf-8")
time.sleep({sleep!r})
sys.stdout.write({output!r})
sys.exit({code!r})
"""


def _fake_gradle(tmp_path: Path, output: str = "", exit_code: int = 0,
                 sleep: float = 0.0) -> Path:
    """Installs a fake gradle wrapper in tmp_path and returns that directory.

    The directory doubles as the module and the gradle root, so tasks stay
    unscoped (resolve_module falls back to a real path, find_gradle_root stops
    at the wrapper).
    """
    payload = tmp_path / "fake_gradle.py"
    payload.write_text(
        _PAYLOAD.format(
            argv=str(tmp_path / ARGV_FILE),
            sleep=sleep,
            output=output,
            code=exit_code,
        ),
        encoding="utf-8",
    )
    if os.name == "nt":
        wrapper = tmp_path / "gradlew.bat"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{payload}" %*\r\n'
            f"exit /b %ERRORLEVEL%\r\n",
            encoding="utf-8",
        )
    else:
        wrapper = tmp_path / "gradlew"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{payload}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    return tmp_path


def _argv(tmp_path: Path) -> list[str]:
    return (tmp_path / ARGV_FILE).read_text(encoding="utf-8").splitlines()


# --------------------------------------------------------------------------
# The kind guard. find_gradle_root walks UP, so a non-gradle project sitting
# under a gradle workspace resolves a DIFFERENT project's wrapper.
# --------------------------------------------------------------------------

def _seed(monkeypatch, root: Path, module: dict) -> None:
    from toolsmith import modules
    monkeypatch.setattr(modules, "_inventory",
                        lambda: (root, [module], {module["shorthand"]: module},
                                 {module["name"]: module}))


def test_a_non_gradle_project_is_refused_before_the_wrapper_walk(tmp_path, monkeypatch):
    """The wrapper above it belongs to something else, and running it is worse
    than refusing: a real build of the wrong project, reported under this name."""
    root = _fake_gradle(tmp_path)
    (root / "toolsmith").mkdir()
    _seed(monkeypatch, root,
          {"shorthand": "ts", "name": "toolsmith", "path": "toolsmith", "kind": "python",
           "repo": True, "package": None, "buildable": False})

    result = gradle_verify("toolsmith")

    assert result["ok"] is False
    assert "python" in result["error"]
    # The strong half: the wrapper above is REAL and would have written this.
    assert not (root / ARGV_FILE).exists()


def test_a_module_named_by_path_is_not_refused_for_having_no_kind(tmp_path, monkeypatch):
    """resolve_module accepts a bare path the inventory never heard of, and
    kind_of answers None for it - which is absence, not a wrong answer."""
    root = _fake_gradle(tmp_path)
    _seed(monkeypatch, root,
          {"shorthand": "other", "name": "other", "path": "other", "kind": "python",
           "repo": False, "package": None, "buildable": False})

    result = gradle_verify(str(root))

    assert result["ok"] is True
    assert (root / ARGV_FILE).exists()


# --------------------------------------------------------------------------
# Failure reporting. This path had no coverage at all - the gate had only ever
# been exercised green-on-green, so nothing proved it could report a red build.
# --------------------------------------------------------------------------

def test_failing_build_reports_exit_code_and_first_failure(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output=(
            "> Task :compileJava\n"
            "Widget.java:12: error: cannot find symbol\n"
            "Widget.java:19: error: incompatible types\n"
            "2 errors\n"
        ),
        exit_code=1,
    )
    result = gradle_verify(str(root))

    assert result["ok"] is False
    assert result["exit_code"] == 1
    assert result["first_failure"] == "Widget.java:12: error: cannot find symbol"
    assert result["lines_kind"] == "signal"
    assert "Widget.java:19: error: incompatible types" in result["lines"]


def test_nonzero_exit_without_diagnostics_still_fails(tmp_path):
    """A red build must fail the gate even when nothing matches as signal."""
    root = _fake_gradle(tmp_path, output="something inscrutable happened\n", exit_code=137)
    result = gradle_verify(str(root))

    assert result["ok"] is False
    assert result["exit_code"] == 137
    assert result["first_failure"] is None
    assert result["lines_kind"] == "tail"


def test_diagnostics_win_over_trailing_chatter(tmp_path):
    """The error must survive even when later output would fill the tail."""
    chatter = "".join(f"trailing line {i}\n" for i in range(40))
    root = _fake_gradle(
        tmp_path,
        output=f"Widget.java:3: error: bad\n{chatter}",
        exit_code=1,
    )
    result = gradle_verify(str(root))

    assert result["lines_kind"] == "signal"
    assert result["first_failure"] == "Widget.java:3: error: bad"
    assert not any("trailing line" in ln for ln in result["lines"])


def test_test_failures_are_surfaced(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output=(
            "WidgetTest > breaks FAILED\n"
            "    java.lang.AssertionError: expected 2\n"
            "FAILURE: Build failed with an exception.\n"
        ),
        exit_code=1,
    )
    result = gradle_verify(str(root))

    assert result["ok"] is False
    assert result["first_failure"] == "WidgetTest > breaks FAILED"
    assert result["lines_kind"] == "signal"


# --------------------------------------------------------------------------
# lines / lines_kind. A passing build's tail is arbitrary chatter, and reading
# it as a summary is exactly the mistake lines_kind exists to prevent.
# --------------------------------------------------------------------------

def test_passing_build_tail_is_labelled_not_signal(tmp_path):
    """Regression guard: a subprocess's own tally must not read as a verdict.

    The annotations gate runs a showcase jar with inheritIO(), so a line like
    this lands in gradle's output on a fully passing run. It counts the
    showcase's cases, not the suite's, and it must never be mistaken for either
    a failure or a test tally.
    """
    root = _fake_gradle(tmp_path, output="48 cases, 0 failures\n", exit_code=0)
    result = gradle_verify(str(root))

    assert result["ok"] is True
    assert result["exit_code"] == 0
    assert result["first_failure"] is None
    assert result["lines_kind"] == "tail"
    assert result["lines"] == ["48 cases, 0 failures"]


def test_silent_pass_is_labelled_empty(tmp_path):
    """An empty tail is normal under -q, not evidence the build no-opped."""
    root = _fake_gradle(tmp_path, output="", exit_code=0)
    result = gradle_verify(str(root))

    assert result["ok"] is True
    assert result["lines"] == []
    assert result["lines_kind"] == "empty"


def test_noise_only_output_is_labelled_empty(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output=(
            "> Task :compileJava\n"
            "Note: some input files use unchecked operations\n"
            "warning: [options] deprecated\n"
            "BUILD SUCCESSFUL in 3s\n"
        ),
        exit_code=0,
    )
    result = gradle_verify(str(root))

    assert result["ok"] is True
    assert result["lines"] == []
    assert result["lines_kind"] == "empty"


def test_tail_is_capped(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output="".join(f"chatter {i}\n" for i in range(50)),
        exit_code=0,
    )
    result = gradle_verify(str(root), tail=5)

    assert len(result["lines"]) == 5
    assert result["lines"][-1] == "chatter 49"
    assert result["lines_kind"] == "tail"


def test_signal_is_capped(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output="".join(f"W.java:{i}: error: e{i}\n" for i in range(30)),
        exit_code=1,
    )
    result = gradle_verify(str(root), tail=4)

    assert len(result["lines"]) == 4
    assert result["first_failure"] == "W.java:0: error: e0"


# --------------------------------------------------------------------------
# Gradle's reason. A configuration failure prints nothing that matches a
# diagnostic but the 'FAILURE:' verdict, so the gate reported rc=1 and that
# line alone while gradle had named the cause under '* What went wrong:'.
#
# The output below is real gradle 9.4.1 under -q --console=plain: the first
# three from the utils module, the last two from a throwaway project holding
# one failing test and one compile error, with its long path shortened. It is
# written with \n, and the stand-in wrapper prints in text mode, so on Windows
# it arrives CRLF-terminated as gradle's own did.
# --------------------------------------------------------------------------

_TESTS_ON_A_COMPILE_TASK = """
FAILURE: Build failed with an exception.

* What went wrong:
Problem configuring task :compileJava from command line.
> Unknown command-line option '--tests'.

* Try:
> Run gradlew help --task :compileJava to get task usage details.
> Run with --stacktrace option to get the stack trace.
> Run with --info or --debug option to get more log output.
> Run with --scan to get full insights from a Build Scan (powered by Develocity).
> Get more help at https://help.gradle.org.

BUILD FAILED in 542ms
"""

_UNKNOWN_TASK = """
FAILURE: Build failed with an exception.

* What went wrong:
Task 'noSuchTask' not found in root project 'utils'.

* Try:
> Run gradlew tasks to get a list of available tasks.
> For more on name expansion, please refer to https://docs.gradle.org/9.4.1/userguide/command_line_interface.html#sec:name_abbreviation in the Gradle documentation.
> Run with --stacktrace option to get the stack trace.
> Run with --info or --debug option to get more log output.
> Run with --scan to get full insights from a Build Scan (powered by Develocity).
> Get more help at https://help.gradle.org.

BUILD FAILED in 520ms
"""

_NO_TESTS_FOUND = """
FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':test'.
> No tests found for given includes: [*NoSuchTestAnywhere*](--tests filter)

* Try:
> Run with --scan to get full insights from a Build Scan (powered by Develocity).

BUILD FAILED in 1s
"""

_FAILING_TESTS = """
2 tests completed, 1 failed

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':test'.
> There were failing tests. See the report at: file:///C:/probe/build/reports/tests/test/index.html

* Try:
> Run with --scan to get full insights from a Build Scan (powered by Develocity).

BUILD FAILED in 1s
"""

_COMPILE_ERROR = r"""C:\probe\src\main\java\probe\Widget.java:5: error: cannot find symbol
        foo();
        ^
  symbol:   method foo()
  location: class Widget
1 error

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':compileJava'.
> Compilation failed; see the compiler output below.
  C:\probe\src\main\java\probe\Widget.java:5: error: cannot find symbol
          foo();
          ^
    symbol:   method foo()
    location: class Widget
  1 error

* Try:
> Check your code and dependencies to fix the compilation error(s)
> Run with --scan to get full insights from a Build Scan (powered by Develocity).

BUILD FAILED in 558ms
"""


def test_an_option_the_task_does_not_take_reports_gradles_reason(tmp_path):
    """--tests after compileJava fails in configuration, before any task runs.

    The exact lines are the point as much as first_failure: the block is kept
    and '* Try:' and 'BUILD FAILED' stay out.
    """
    root = _fake_gradle(tmp_path, output=_TESTS_ON_A_COMPILE_TASK, exit_code=1)
    result = gradle_verify(str(root), tasks=["compileJava"], tests=["*PossibleTest*"])

    assert result["ok"] is False
    assert result["first_failure"] == ("Problem configuring task :compileJava from command line. "
                                       "> Unknown command-line option '--tests'.")
    assert result["lines_kind"] == "signal"
    assert result["lines"] == ["FAILURE: Build failed with an exception.",
                               "* What went wrong:",
                               "Problem configuring task :compileJava from command line.",
                               "> Unknown command-line option '--tests'."]


def test_an_unknown_task_reports_gradles_reason(tmp_path):
    root = _fake_gradle(tmp_path, output=_UNKNOWN_TASK, exit_code=1)
    result = gradle_verify(str(root), tasks=["noSuchTask"])

    assert result["first_failure"] == "Task 'noSuchTask' not found in root project 'utils'."
    assert result["lines"] == ["FAILURE: Build failed with an exception.",
                               "* What went wrong:",
                               "Task 'noSuchTask' not found in root project 'utils'."]


def test_a_test_filter_matching_nothing_reports_gradles_reason(tmp_path):
    """An execution failure of the test task names its cause nowhere else either."""
    root = _fake_gradle(tmp_path, output=_NO_TESTS_FOUND, exit_code=1)
    result = gradle_verify(str(root), tasks=["test"], tests=["*NoSuchTestAnywhere*"])

    assert result["first_failure"] == (
        "Execution failed for task ':test'. "
        "> No tests found for given includes: [*NoSuchTestAnywhere*](--tests filter)")


def test_failing_tests_under_quiet_report_the_task_that_failed(tmp_path):
    """-q prints no per-test FAILED line, so the reason is the first thing naming the failure.

    The count line was never a diagnostic and still is not one.
    """
    root = _fake_gradle(tmp_path, output=_FAILING_TESTS, exit_code=1)
    result = gradle_verify(str(root), tasks=["test"])

    assert result["first_failure"] == (
        "Execution failed for task ':test'. > There were failing tests. "
        "See the report at: file:///C:/probe/build/reports/tests/test/index.html")
    assert "2 tests completed, 1 failed" not in result["lines"]


@pytest.mark.parametrize("output, first", [
    (_COMPILE_ERROR, r"C:\probe\src\main\java\probe\Widget.java:5: error: cannot find symbol"),
    # What a build logging test events past -q prints ahead of the same tail.
    ("WidgetTest > breaks() FAILED\n"
     "    org.opentest4j.AssertionFailedError at WidgetTest.java:10\n" + _FAILING_TESTS,
     "WidgetTest > breaks() FAILED"),
], ids=["compile-error", "failing-test"])
def test_a_diagnostic_printed_before_the_verdict_stays_the_first_failure(tmp_path, output,
                                                                        first):
    root = _fake_gradle(tmp_path, output=output, exit_code=1)
    result = gradle_verify(str(root))

    assert result["first_failure"] == first
    assert result["lines"][0] == first
    assert "* What went wrong:" in result["lines"]


def test_a_compile_error_keeps_the_compiler_output_gradle_repeats_in_its_reason(tmp_path):
    """Gradle 9 repeats the compiler output under the heading, and the block is kept whole."""
    root = _fake_gradle(tmp_path, output=_COMPILE_ERROR, exit_code=1)
    result = gradle_verify(str(root))

    assert result["lines"] == [
        r"C:\probe\src\main\java\probe\Widget.java:5: error: cannot find symbol",
        "FAILURE: Build failed with an exception.",
        "* What went wrong:",
        "Execution failed for task ':compileJava'.",
        "> Compilation failed; see the compiler output below.",
        r"  C:\probe\src\main\java\probe\Widget.java:5: error: cannot find symbol",
        "          foo();",
        "          ^",
        "    symbol:   method foo()",
        "    location: class Widget",
        "  1 error",
    ]


def test_the_reason_ends_at_the_next_section_without_a_blank_line(tmp_path):
    root = _fake_gradle(
        tmp_path,
        output=("FAILURE: Build failed with an exception.\n"
                "* What went wrong:\n"
                "Execution failed for task ':a'.\n"
                "> boom\n"
                "* Try:\n"
                "> Run with --stacktrace option to get the stack trace.\n"),
        exit_code=1,
    )
    result = gradle_verify(str(root))

    assert result["first_failure"] == "Execution failed for task ':a'. > boom"
    assert result["lines"][-1] == "> boom"


def test_the_verdict_is_the_first_failure_only_when_no_reason_follows(tmp_path):
    root = _fake_gradle(tmp_path, output="FAILURE: Build failed with an exception.\n",
                        exit_code=1)
    result = gradle_verify(str(root))

    assert result["first_failure"] == "FAILURE: Build failed with an exception."


def test_the_cli_prints_gradles_reason(tmp_path, capsys):
    root = _fake_gradle(tmp_path, output=_TESTS_ON_A_COMPILE_TASK, exit_code=1)

    code = cli.main(["gradle", "verify", str(root), "compileJava", "--tests", "*PossibleTest*"])

    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert "> Unknown command-line option '--tests'." in out
    assert out[-1] == "GATE: FAIL rc=1"


# --------------------------------------------------------------------------
# Timeout backstop. With a pipe sink this path can itself hang on Windows
# (subprocess.run drains with communicate() and no timeout after the kill), so
# it is worth proving it returns a verdict.
# --------------------------------------------------------------------------

def test_timeout_fails_the_gate(tmp_path):
    root = _fake_gradle(tmp_path, output="never gets here\n", exit_code=0, sleep=30.0)
    result = gradle_verify(str(root), timeout=1.0)

    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "timed out" in result["first_failure"]


# --------------------------------------------------------------------------
# Invocation shape.
# --------------------------------------------------------------------------

def test_default_tasks_are_compile_and_test(tmp_path):
    root = _fake_gradle(tmp_path)
    result = gradle_verify(str(root))

    assert _argv(root)[:2] == ["compileJava", "test"]
    assert result["tasks"] == ["compileJava", "test"]


def test_compile_only_swaps_the_task_set(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), compile_only=True)

    assert _argv(root)[:2] == ["compileJava", "compileTestJava"]


def test_rerun_tasks_appends_flag_and_leaves_tasks_alone(tmp_path):
    """--rerun-tasks is a flag, not a task: it must never be task-scoped."""
    root = _fake_gradle(tmp_path)
    result = gradle_verify(str(root), tasks=["test"], rerun_tasks=True)

    argv = _argv(root)
    assert "--rerun-tasks" in argv
    assert "--rerun" not in argv
    assert result["tasks"] == ["test"]
    assert not any(a.startswith(":") for a in argv)


def test_both_reruns_default_off(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test"])

    argv = _argv(root)
    assert "--rerun-tasks" not in argv
    assert "--rerun" not in argv


def test_quiet_and_plain_console_always_passed(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test"])

    argv = _argv(root)
    assert "-q" in argv
    assert "--console=plain" in argv


def test_unresolvable_module_is_an_error_not_a_crash(tmp_path):
    result = gradle_verify(str(tmp_path / "does-not-exist"))

    assert result["ok"] is False
    assert "error" in result


def test_missing_wrapper_is_an_error(tmp_path):
    """A real directory with no gradle wrapper anywhere above it."""
    result = gradle_verify(str(tmp_path))

    assert result["ok"] is False
    assert "error" in result


def test_an_empty_task_list_takes_the_defaults(tmp_path):
    """An empty list names no task, the same as an omitted one."""
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=[])

    assert _argv(root)[:2] == ["compileJava", "test"]


def test_the_result_carries_the_argv_gradle_received(tmp_path):
    root = _fake_gradle(tmp_path)
    result = gradle_verify(str(root), tasks=["test"], tests=["*A"], system_properties=["k=v"])

    assert result["argv"] == _argv(root)


# --------------------------------------------------------------------------
# Task options and system properties. Gradle binds a task option to the task
# written immediately before it, so --tests and --rerun must land straight
# after the last task; a -D has no position.
# --------------------------------------------------------------------------

def test_task_options_follow_the_last_task_in_the_order_given(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["compileJava", "test"],
                  tests=["*WidgetTest", "Gadget.spins"], rerun=True)

    assert _argv(root) == ["compileJava", "test",
                           "--tests", "*WidgetTest", "--tests", "Gadget.spins", "--rerun",
                           "-q", "--console=plain"]


def test_task_options_bind_to_test_when_no_task_is_named(tmp_path):
    """The default set ends in test, which is what a filter on the gate means."""
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tests=["*WidgetTest"], rerun=True)

    assert _argv(root)[:5] == ["compileJava", "test", "--tests", "*WidgetTest", "--rerun"]


def test_a_named_task_carries_task_options_under_compile_only(tmp_path):
    """compile_only picks a DEFAULT set; a named task replaces it."""
    root = _fake_gradle(tmp_path)
    result = gradle_verify(str(root), tasks=["test"], compile_only=True, tests=["*A"])

    assert result["ok"] is True
    assert _argv(root)[:3] == ["test", "--tests", "*A"]


def test_rerun_binds_to_the_task_and_rerun_tasks_to_the_build(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test"], rerun=True, rerun_tasks=True)

    argv = _argv(root)
    assert argv[:2] == ["test", "--rerun"]
    assert argv.index("--rerun-tasks") > argv.index("--console=plain")


def test_system_properties_become_dash_d_flags_after_everything_else(tmp_path):
    """Repeatable, in order, and an '=' inside the value belongs to the value."""
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=[":test"], tests=["*ParityReferencesTest"], rerun=True,
                  system_properties=["asset.parity.regenerateViews=true", "empty=",
                                     "joined=a=b", "spaced=two words"])

    assert _argv(root) == [":test", "--tests", "*ParityReferencesTest", "--rerun",
                           "-q", "--console=plain",
                           "-Dasset.parity.regenerateViews=true", "-Dempty=",
                           "-Djoined=a=b", "-Dspaced=two words"]


def test_a_wildcard_argument_is_quoted_on_the_windows_command_line():
    """java.exe on Windows globs an UNQUOTED * or ? against the gradle root's files.

    Case-insensitively, so --tests *Parity* in a root holding parity/ reached
    gradle as --tests parity. Quoted, the launcher strips the quotes and
    leaves the pattern alone; everything without a wildcard is quoted exactly
    as list2cmdline would.
    """
    line = _windows_command_line(["C:/w/gradlew.bat", ":test", "--tests", "*Parity*",
                                  "--tests", "Gadget?", "--tests", "trail*\\",
                                  "-Dk=two words*", "-Dplain=v", "--rerun"])

    assert line == ('C:/w/gradlew.bat :test --tests "*Parity*" --tests "Gadget?" '
                    '--tests "trail*\\\\" "-Dk=two words*" -Dplain=v --rerun')


def test_a_quoted_wildcard_reaches_the_wrapper_unquoted(tmp_path):
    """The quoting is for the launcher only - what arrives is the pattern itself."""
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test"], tests=["*Widget*", "Gadget?", "trail*\\"],
                  system_properties=["k=two words*"])

    assert _argv(root) == ["test", "--tests", "*Widget*", "--tests", "Gadget?",
                           "--tests", "trail*\\", "-q", "--console=plain", "-Dk=two words*"]


def test_task_options_are_never_task_scoped_for_a_subproject(tmp_path, monkeypatch):
    """Only the tasks take the ':module:' prefix - an option given it is a task name."""
    root = _fake_gradle(tmp_path)
    (root / "widgets").mkdir()
    _seed(monkeypatch, root,
          {"shorthand": "w", "name": "widgets", "path": "widgets", "kind": "gradle",
           "repo": False, "package": None, "buildable": True})

    result = gradle_verify("widgets", tasks=["test"], tests=["*A"], rerun=True,
                           system_properties=["k=v"])

    assert result["tasks"] == [":widgets:test"]
    assert _argv(root) == [":widgets:test", "--tests", "*A", "--rerun",
                           "-q", "--console=plain", "-Dk=v"]


# --------------------------------------------------------------------------
# Scoping. A module without a wrapper of its own is a project of the build
# above it, named by the project path its directory spells under that build's
# root - never by the alias or name the caller typed, which gradle accepts
# only as an abbreviation that happens to match.
# --------------------------------------------------------------------------

def _wrapperless(tmp_path: Path, monkeypatch, build: str, path: str, shorthand: str) -> Path:
    """Seeds one module at tmp_path/path under a stand-in wrapper at tmp_path/build.

    Returns:
        the directory holding the wrapper, where the argv file lands
    """
    root = tmp_path / build if build else tmp_path
    root.mkdir(exist_ok=True)
    _fake_gradle(root)
    (tmp_path / path).mkdir(parents=True)
    name = Path(path).name
    _seed(monkeypatch, tmp_path,
          {"shorthand": shorthand, "name": name, "path": path, "kind": "gradle",
           "repo": False, "package": None, "buildable": True})
    return root


@pytest.mark.parametrize("task, sent", [
    ("test", ":tooling:test"),
    (":test", ":tooling:test"),
    (":sub:test", ":tooling:sub:test"),
])
def test_a_task_is_relative_to_the_module_named(tmp_path, monkeypatch, task, sent):
    """':test' is the module's own test, as it is for a module that is its own root."""
    root = _wrapperless(tmp_path, monkeypatch, "", "tooling", "too")

    result = gradle_verify("tooling", tasks=[task])

    assert result["tasks"] == [sent]
    assert _argv(root)[0] == sent


def test_a_subproject_is_scoped_by_its_directory_not_by_the_alias(tmp_path, monkeypatch):
    """'lib' reached ':library' only as an abbreviation gradle chose to accept."""
    root = _wrapperless(tmp_path, monkeypatch, "annotations", "annotations/library", "lib")

    result = gradle_verify("lib", tasks=[":help"])

    assert result["tasks"] == [":library:help"]
    assert _argv(root)[0] == ":library:help"


def test_a_nested_subproject_takes_every_segment_of_its_directory(tmp_path, monkeypatch):
    root = _wrapperless(tmp_path, monkeypatch, "", "a/b", "b")

    gradle_verify("b", tasks=["compileJava", ":test"])

    assert _argv(root)[:2] == [":a:b:compileJava", ":a:b:test"]


def test_a_subproject_named_by_path_is_scoped_by_its_directory(tmp_path):
    """The path token is not a project name, and was spliced in as one."""
    root = _fake_gradle(tmp_path)
    (root / "widgets").mkdir()

    result = gradle_verify(str(root / "widgets"), tasks=[":test"])

    assert result["tasks"] == [":widgets:test"]


def test_a_root_named_by_a_relative_path_takes_its_tasks_as_written(tmp_path, monkeypatch):
    """'.' resolves to the root itself, which is no subproject of anything."""
    root = _fake_gradle(tmp_path)
    monkeypatch.chdir(root)

    result = gradle_verify(".", tasks=["test"])

    assert result["tasks"] == ["test"]


def test_a_root_module_takes_a_project_path_as_written(tmp_path):
    root = _fake_gradle(tmp_path)

    result = gradle_verify(str(root), tasks=[":tooling:test", ":test", "test"])

    assert result["tasks"] == [":tooling:test", ":test", "test"]


def test_a_flag_in_tasks_is_not_scoped_for_a_subproject(tmp_path, monkeypatch):
    root = _wrapperless(tmp_path, monkeypatch, "", "tooling", "too")

    gradle_verify("too", tasks=["test", "--info"])

    assert _argv(root)[:2] == [":tooling:test", "--info"]


def test_the_cli_runs_a_subprojects_own_test_for_a_leading_colon(tmp_path, monkeypatch, capsys):
    root = _wrapperless(tmp_path, monkeypatch, "", "tooling", "too")

    code = cli.main(["gradle", "verify", "tooling", ":test", "--tests", "*A"])

    assert code == 0
    assert _argv(root)[:3] == [":tooling:test", "--tests", "*A"]
    assert "gradlew :tooling:test --tests '*A'" in capsys.readouterr().err


# --------------------------------------------------------------------------
# The refusals. Each one is decided before anything runs, so the wrapper
# never writes its argv file.
# --------------------------------------------------------------------------

def _refused(root: Path, result: dict) -> str:
    """Asserts a call was refused without running gradle and returns the reason."""
    assert result["ok"] is False
    assert not (root / ARGV_FILE).exists()
    return result["error"]


def test_task_options_are_refused_under_compile_only_with_no_task_named(tmp_path):
    """That default set ends in compileTestJava: no test task to filter or rerun."""
    root = _fake_gradle(tmp_path)

    assert "compile-only" in _refused(root, gradle_verify(str(root), compile_only=True,
                                                          tests=["*A"]))
    assert "compile-only" in _refused(root, gradle_verify(str(root), compile_only=True,
                                                          rerun=True))


def test_a_flag_in_tasks_is_refused_beside_task_options(tmp_path):
    root = _fake_gradle(tmp_path)

    error = _refused(root, gradle_verify(str(root), tasks=["test", "--info"], tests=["*A"]))
    assert "'--info'" in error


def test_a_flag_in_tasks_still_runs_without_task_options(tmp_path):
    """The refusal guards the binding, so a run with nothing to bind is left alone."""
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test", "--info"])

    assert _argv(root)[:2] == ["test", "--info"]


@pytest.mark.parametrize("pattern, reason", [
    ("", "empty"),
    ("   ", "empty"),
    ("-WidgetTest", "starts with '-'"),
    ('Widget"Test', "cmd.exe"),
    ("Widget%PATH%", "cmd.exe"),
    ("Widget&calc", "cmd.exe"),
    ("Widget|more", "cmd.exe"),
    ("Widget\nTest", "cmd.exe"),
])
def test_a_malformed_test_pattern_is_refused(tmp_path, pattern, reason):
    root = _fake_gradle(tmp_path)

    assert reason in _refused(root, gradle_verify(str(root), tasks=["test"], tests=[pattern]))


def test_a_bare_system_property_is_refused_with_the_spelling_to_use(tmp_path):
    """A bare -DKEY is the empty string, which Boolean.getBoolean reads as false."""
    root = _fake_gradle(tmp_path)

    error = _refused(root, gradle_verify(str(root), system_properties=["regenerateViews"]))
    assert "regenerateViews=true" in error


@pytest.mark.parametrize("entry, reason", [
    ("-Dkey=value", "without the -D"),
    ("=value", "property name"),
    ("two words=value", "property name"),
    ("9lives=value", "property name"),
    ("key=a&calc", "cmd.exe"),
    ("key=%PATH%", "cmd.exe"),
    ('key="quoted"', "cmd.exe"),
    ("key=a\nb", "cmd.exe"),
])
def test_a_malformed_system_property_is_refused(tmp_path, entry, reason):
    root = _fake_gradle(tmp_path)

    assert reason in _refused(root, gradle_verify(str(root), system_properties=[entry]))


def test_an_argument_is_refused_before_the_module_is_resolved(tmp_path):
    """A malformed argument is wrong whichever module it was aimed at."""
    result = gradle_verify(str(tmp_path / "does-not-exist"), system_properties=["bare"])

    assert result["ok"] is False
    assert "bare=true" in result["error"]


@pytest.mark.parametrize("task", [
    "test&calc", "test|more", "test>out", "test<in", "test%PATH%", "test^x", "test!x",
    'te"st', "test\nx",
])
def test_a_task_carrying_a_cmd_metacharacter_is_refused(tmp_path, task):
    """gradlew.bat hands the command line to cmd.exe, so 'test&calc' would run calc."""
    root = _fake_gradle(tmp_path)

    error = _refused(root, gradle_verify(str(root), tasks=["compileJava", task]))
    assert f"task '{task}'" in error
    assert "cmd.exe" in error


def test_a_task_is_refused_before_the_module_is_resolved(tmp_path):
    result = gradle_verify(str(tmp_path / "does-not-exist"), tasks=["test&calc"])

    assert result["ok"] is False
    assert "task 'test&calc'" in result["error"]


def test_a_task_path_of_gradle_characters_is_passed(tmp_path):
    root = _fake_gradle(tmp_path)

    gradle_verify(str(root), tasks=[":a-b_c.d:compileTestJava", "-Pkey=value"])

    assert _argv(root)[:2] == [":a-b_c.d:compileTestJava", "-Pkey=value"]


def test_a_cli_task_refusal_is_a_precondition_exit(tmp_path, capsys):
    root = _fake_gradle(tmp_path)

    code = cli.main(["gradle", "verify", str(root), "test&calc"])

    assert code == 2
    assert "task 'test&calc'" in capsys.readouterr().err
    assert not (root / ARGV_FILE).exists()


# --------------------------------------------------------------------------
# The CLI row and the MCP tool. Both forward to gradle_verify, so what is
# pinned here is that every argument reaches it - through argparse on one side
# and through the tool's schema on the other.
# --------------------------------------------------------------------------

def test_the_cli_parses_task_options_and_repeatable_properties():
    args = cli.build_parser().parse_args(
        ["gradle", "verify", "utils", ":test", "--tests", "*A", "--tests", "B",
         "--rerun", "--rerun-tasks", "-Da.b=c", "-D", "x=y"])

    assert (args.module, args.tasks, args.tests, args.rerun, args.rerun_tasks,
            args.system_properties) == ("utils", [":test"], ["*A", "B"], True, True,
                                        ["a.b=c", "x=y"])


def test_the_cli_runs_the_command_it_prints(tmp_path, capsys):
    """The printed command is on stderr, and it is the argv gradle received."""
    root = _fake_gradle(tmp_path)

    code = cli.main(["gradle", "verify", str(root), ":test", "--tests", "*Widget*",
                     "--rerun", "-Dk=v"])

    captured = capsys.readouterr()
    assert code == 0
    assert _argv(root) == [":test", "--tests", "*Widget*", "--rerun",
                           "-q", "--console=plain", "-Dk=v"]
    assert "gradlew :test --tests '*Widget*' --rerun -q --console=plain -Dk=v" in captured.err
    assert captured.out.splitlines()[-1] == "GATE: PASS rc=0"


def test_a_cli_refusal_is_a_precondition_exit(tmp_path, capsys):
    root = _fake_gradle(tmp_path)

    code = cli.main(["gradle", "verify", str(root), "-D", "bare"])

    assert code == 2
    assert "bare=true" in capsys.readouterr().err
    assert not (root / ARGV_FILE).exists()


def test_the_mcp_tool_forwards_every_argument_to_gradle(tmp_path):
    """The tool function is what the server calls; the schema test below pins
    that the server advertises each of these names."""
    root = _fake_gradle(tmp_path)

    server.gradle_verify(str(root), tasks=[":test"], tests=["*A", "B"], rerun=True,
                         rerun_tasks=True, system_properties=["k=v", "e="])

    assert _argv(root) == [":test", "--tests", "*A", "--tests", "B", "--rerun",
                           "-q", "--console=plain", "--rerun-tasks", "-Dk=v", "-De="]


def test_the_mcp_tool_returns_a_refusal_rather_than_running(tmp_path):
    root = _fake_gradle(tmp_path)

    result = server.gradle_verify(str(root), compile_only=True, tests=["*A"])

    assert "compile-only" in _refused(root, result)


def test_the_mcp_tool_states_the_binding_rule_where_an_agent_reads_it():
    """The docstring is the tool description, which is what gets matched and read."""
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    tool = tools["gradle_verify"]

    assert {"tests", "rerun", "rerun_tasks", "system_properties"} <= set(
        tool.inputSchema["properties"])
    assert "LAST task" in tool.description
    assert "immediately before" in tool.description
