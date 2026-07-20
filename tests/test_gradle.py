"""Tests for the module-scoped gradle gate.

These drive a stand-in gradle wrapper rather than a real build, so the actual
subprocess plumbing is exercised - file sink, exit-code capture, timeout
backstop, signal extraction - without needing Gradle or a JDK.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from toolsmith.gradle import gradle_verify

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


def test_rerun_appends_flag_and_leaves_tasks_alone(tmp_path):
    """--rerun-tasks is a flag, not a task: it must never be task-scoped."""
    root = _fake_gradle(tmp_path)
    result = gradle_verify(str(root), tasks=["test"], rerun=True)

    argv = _argv(root)
    assert "--rerun-tasks" in argv
    assert result["tasks"] == ["test"]
    assert not any(a.startswith(":") for a in argv)


def test_rerun_defaults_off(tmp_path):
    root = _fake_gradle(tmp_path)
    gradle_verify(str(root), tasks=["test"])

    assert "--rerun-tasks" not in _argv(root)


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
