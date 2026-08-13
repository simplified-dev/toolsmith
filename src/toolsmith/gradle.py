"""Module-scoped gradle verification with reliable exit codes and noise stripping.

Replaces the single most-repeated shell shape in the workspace history:

    cd "W:/.../module" && ./gradlew compileJava test -q 2>&1 \
        | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS[0]}"

Captures gradle's true return code from an unpiped run (never the fragile
``${PIPESTATUS}`` through a ``grep | tail`` pipeline), strips the standard
noise, and prefers real signal lines (compile errors, test failures) over a
blind tail.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile

from .modules import find_gradle_root, kind_of, resolve_module, workspace_root

# Hard backstop so a wedged build fails the gate instead of hanging forever.
# Kept well above a real cold cross-JDK run (single-digit minutes) but low
# enough that a wedge - e.g. the client blocking in its native console probe
# before it ever reaches the daemon - surfaces while it is still actionable
# rather than burning half an hour. Callers with a genuinely long gate can
# raise it per call via the timeout argument.
_TIMEOUT_SECONDS = 600

# Windows only: give the client a console of its own rather than the one the
# MCP server hands down.
#
# The gradlew client probes whether its stdin is a terminal
# (RunBuildAction -> NativePlatformConsoleDetector.isConsoleInput ->
# WindowsConsoleFunctions.isConsole). Windows console APIs are ALPC calls into
# conhost.exe, and against the inherited console that probe blocks forever -
# the thread sits RUNNABLE in the native frame burning no CPU, and the build
# never reaches the daemon. Handing the child a fresh console clears it.
#
# CREATE_NO_WINDOW rather than DETACHED_PROCESS, though both clear the wedge:
# gradlew is a .bat, so it runs under cmd.exe, and cmd.exe always wants a
# console. DETACHED_PROCESS only denies it the inherited one, so Windows grants
# a brand-new *visible* one - a console window flashes on screen for every
# build. CREATE_NO_WINDOW grants the same fresh console without a window.
_NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# Lines every hand-written gradle invocation strips.
_NOISE = re.compile(
    r"incubating|Deprecated Gradle features|will fail with an error in Gradle"
    r"|warning:|^Note:|^> Task |^BUILD |Daemon will be stopped|Configure project"
)

# Lines worth surfacing first on a failure.
_SIGNAL = re.compile(
    r"error:|\.java:\d+|FAILED|FAILURE:|Compilation failed|There were failing tests"
    r"|Test .* FAILED|^Caused by:|Exception"
)


def gradle_verify(
    module: str,
    tasks: list[str] | None = None,
    tail: int = 25,
    compile_only: bool = False,
    rerun: bool = False,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict:
    """Runs the module-scoped gradle gate and returns a structured verdict.

    Args:
        module: module alias, name, or path (see toolsmith.modules).
        tasks: gradle tasks to run. Defaults to compileJava+test, or
            compileJava+compileTestJava when compile_only is set.
        tail: how many signal (or de-noised trailing) lines to return.
        compile_only: use the compile-only default task set.
        rerun: pass --rerun-tasks so tasks re-run past up-to-date checks and
            the build cache (avoids FROM-CACHE - the tests actually execute).
            Note that clean does NOT do this: it only deletes build/, which
            the build cache immediately restores.
        timeout: seconds before the build is killed and the gate fails.

    Returns:
        dict with module, tasks, root, exit_code, ok, first_failure, lines,
        lines_kind. On timeout, ok is False, exit_code is None, and timed_out
        is True.

        lines_kind says what `lines` actually holds, and must be checked before
        reading anything into it:
          "signal" - lines matched as failure diagnostics. Meaningful.
          "tail"   - no diagnostics matched, so this is just the last few
                     non-noise lines the build happened to print. It is NOT a
                     summary or a verdict, and on a passing run it is usually
                     unrelated chatter from whatever the build shelled out to.
          "empty"  - the build printed nothing after noise stripping. Normal
                     for a clean run under -q; not evidence the build no-opped.

        In particular `lines` is never a test tally. Counts printed there come
        from whatever the build spawned, not from gradle_verify - use
        gradle_tally or the JUnit XML for real numbers.
    """
    if tasks is None:
        tasks = ["compileJava", "compileTestJava"] if compile_only else ["compileJava", "test"]

    mod_dir = resolve_module(module)
    if mod_dir is None:
        return {"module": module, "ok": False,
                "error": f"module '{module}' not resolved (run 'toolsmith setup'); root={workspace_root()}"}

    # Refused BEFORE the walk, because the walk goes UP: a python project with a
    # gradle workspace above it resolves that workspace's wrapper and runs a
    # DIFFERENT project's build rather than failing. An unrecorded kind is not a
    # wrong one - see modules.kind_of - so only a known non-gradle kind refuses.
    kind = kind_of(module)
    if kind is not None and kind != "gradle":
        return {"module": module, "ok": False,
                "error": f"'{module}' is a {kind} project - a gradle wrapper found above it "
                         f"belongs to a different project"}

    root = find_gradle_root(mod_dir)
    if root is None:
        return {"module": module, "ok": False,
                "error": f"no gradle wrapper found upward from {mod_dir}"}

    # A standalone repo IS the root project -> unscoped tasks. Only a genuine
    # subproject (root above the module dir) needs the ':module:task' form.
    invoke = tasks if root == mod_dir else [f":{module}:{t}" for t in tasks]
    gradlew = str(root / ("gradlew.bat" if os.name == "nt" else "gradlew"))

    # Base flags. --rerun-tasks goes HERE, not in `invoke`: for a subproject
    # every `invoke` element is wrapped as ':module:task', which would mangle a
    # flag. It forces re-execution past both up-to-date checks and the build
    # cache, so tests actually run instead of being restored FROM-CACHE.
    flags = ["-q", "--console=plain"]
    if rerun:
        flags.append("--rerun-tasks")

    # Redirect gradle's output to a temp FILE and wait on the process, rather
    # than capture_output=True (OS pipes). Two reasons, both about waiting on
    # the pipe's EOF rather than on the child's exit:
    #
    # 1. A surviving descendant can hold the write handle open. Anything that
    #    inherits gradle's stdout - the daemon, a forked test worker, or a test
    #    that spawns its own subprocess with ProcessBuilder.inheritIO() - keeps
    #    that handle alive after the client exits, so the read blocks on an EOF
    #    that never arrives even though the build already finished. Not
    #    hypothetical: the annotations gate runs a test that launches the
    #    showcase jar with inheritIO(), and its output lands in this log.
    # 2. It is what makes `timeout` trustworthy. On a timeout CPython kills the
    #    child and then, on Windows only, calls communicate() a second time with
    #    NO timeout to drain the pipes (see subprocess.run, the _mswindows
    #    branch). Given (1) that drain can never return - so with pipes the
    #    backstop meant to turn a wedge into ok=False would itself hang. A file
    #    sink has nothing to drain.
    #
    # A file wait keys off the direct child's exit and is immune to both; it is
    # also what the original bash gate did before the Python port introduced
    # capture_output. This is a DIFFERENT failure from the console-probe wedge
    # handled by _NO_CONSOLE: that one blocks the client before any daemon work
    # begins, this one blocks the parent after the build has already finished.
    #
    # stdin is DEVNULL as hygiene: under the MCP server our stdin is the stdio
    # JSON-RPC pipe, and a child must never be able to read protocol bytes off
    # it. (Not a fix for any observed hang - an inherited pipe stdin does not
    # by itself wedge the client.) The console-probe wedge is handled by
    # _NO_CONSOLE instead.
    fd, log_path = tempfile.mkstemp(prefix="toolsmith-gradle-", suffix=".log")
    os.close(fd)
    returncode: int | None = None
    timed_out = False
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as sink:
            try:
                proc = subprocess.run(
                    [gradlew, *invoke, *flags],
                    cwd=str(root),
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    creationflags=_NO_CONSOLE,
                    timeout=timeout,
                )
                returncode = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    finally:
        try:
            os.unlink(log_path)
        except OSError:
            pass

    # Signal lines are diagnostics; the tail fallback is only "whatever the
    # build last printed". They are not interchangeable, so the caller is told
    # which one it got - an unlabelled tail reads as a summary and gets
    # believed. See lines_kind in the docstring.
    signal = [ln for ln in lines if _SIGNAL.search(ln) and not _NOISE.search(ln)]
    if signal:
        kept, kind = signal[:tail], "signal"
    else:
        kept = [ln for ln in lines if ln.strip() and not _NOISE.search(ln)][-tail:]
        kind = "tail" if kept else "empty"

    if timed_out:
        return {
            "module": module,
            "tasks": invoke,
            "root": str(root),
            "exit_code": None,
            "ok": False,
            "timed_out": True,
            "first_failure": f"gradle timed out after {timeout:g}s",
            "lines": kept,
            "lines_kind": kind,
        }

    return {
        "module": module,
        "tasks": invoke,
        "root": str(root),
        "exit_code": returncode,
        "ok": returncode == 0,
        "first_failure": signal[0] if signal else None,
        "lines": kept,
        "lines_kind": kind,
    }
