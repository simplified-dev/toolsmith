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

# What a -D entry must look like: a property name as the JVM's own are spelled -
# dotted words - then '=' and the value, which may be empty. The name is held to
# that charset rather than to "anything without an '='" so that a caller who
# writes the flag into the value ('-Dkey=v') is refused instead of becoming
# '-D-Dkey=v'.
_SYSTEM_PROPERTY = re.compile(r"([A-Za-z_][A-Za-z0-9_.\-]*)=(.*)", re.S)

# Characters a test pattern or a property value may not carry. On Windows the
# wrapper is a batch file, so the command line goes through cmd.exe, which
# expands %VAR% even inside quotes and splits on & | < > outside them - and
# CPython escapes none of it for a batch file. A value holding one is either
# mangled or runs a second command, so it is refused on every platform, which
# keeps one call meaning one thing wherever it runs.
_CMD_META = re.compile(r'["%^&|<>!\x00-\x1f]')

# The characters java.exe's Windows launcher globs on.
_WILDCARD = re.compile(r"[*?]")


def _windows_command_line(args: list[str]) -> str:
    """Builds the Windows command line for a gradle run, every wildcard argument quoted.

    On Windows java.exe's launcher expands an UNQUOTED argument holding * or ?
    against the files of its working directory - the gradle root - and matches
    case-insensitively, so ``--tests *Parity*`` in a root holding ``parity/``
    reaches gradle as ``--tests parity``. CPython's list2cmdline quotes an
    argument only for whitespace, so it would hand the pattern over bare.
    cmd.exe passes the quotes through the wrapper's %* untouched and the
    launcher strips them without expanding.

    Args:
        args: the wrapper path and its arguments

    Returns:
        the command line, quoted by list2cmdline's rules plus the wildcard rule
    """
    parts = []
    for arg in args:
        plain = subprocess.list2cmdline([arg])
        if _WILDCARD.search(arg) and not plain.startswith('"'):
            # A run of backslashes before the closing quote is doubled, or the
            # last of them would escape it.
            trailing = len(plain) - len(plain.rstrip("\\"))
            plain = '"' + plain + "\\" * trailing + '"'
        parts.append(plain)
    return " ".join(parts)


def _task_options(tasks: list[str], named: bool, compile_only: bool,
                  tests: list[str], rerun: bool) -> list[str]:
    """Builds the options written straight after the last task of the run.

    Gradle binds a task option to the task immediately before it, so these
    apply to the last task and to no other. When the caller named no task the
    last one is the default set's: `test`, or under compile_only
    `compileTestJava`, which carries neither a filter nor a rerun meant for a
    test - so that combination is refused rather than bound to a compile task.

    Args:
        tasks: the tasks of the run, defaults already applied
        named: whether the caller named those tasks
        compile_only: whether the compile-only default set is in force
        tests: test filter patterns, each becoming --tests PATTERN
        rerun: whether to append gradle's --rerun

    Returns:
        the options, in order

    Raises:
        ValueError: when an option has no task to bind to, a flag rides in
            tasks beside them, or a pattern is malformed
    """
    if not tests and not rerun:
        return []
    if not named and compile_only:
        raise ValueError(
            "--tests and --rerun bind to the last task of the run, and the compile-only "
            "default set (compileJava compileTestJava) has no test task - name the task "
            "they apply to last, e.g. 'test'")
    for task in tasks:
        if task.startswith("-"):
            raise ValueError(
                f"tasks holds '{task}', a flag rather than a task - --tests and --rerun bind "
                f"to the last TASK, so a flag cannot ride in tasks beside them")
    options: list[str] = []
    for pattern in tests:
        if not pattern.strip():
            raise ValueError("--tests needs a pattern, and one given is empty")
        if pattern.startswith("-"):
            raise ValueError(f"--tests pattern '{pattern}' starts with '-', which gradle "
                             f"reads as an option rather than a pattern")
        bad = _CMD_META.search(pattern)
        if bad:
            raise ValueError(f"--tests pattern '{pattern}' holds {bad.group()!r}, which the "
                             f"Windows wrapper's cmd.exe expands or splits on")
        options += ["--tests", pattern]
    if rerun:
        options.append("--rerun")
    return options


def _system_property_flags(system_properties: list[str]) -> list[str]:
    """Turns KEY=VALUE entries into gradle's -DKEY=VALUE flags.

    A bare KEY is refused rather than passed: gradle reads -DKEY as the empty
    string, and Boolean.getBoolean reads that as false, so a bare flag would
    mean the opposite of what it looks like.

    Args:
        system_properties: the entries, each KEY=VALUE

    Returns:
        one -DKEY=VALUE flag per entry, in order

    Raises:
        ValueError: when an entry is not KEY=VALUE or its value carries a
            character the Windows wrapper's cmd.exe acts on
    """
    flags: list[str] = []
    for entry in system_properties:
        if "=" not in entry:
            raise ValueError(
                f"-D '{entry}' has no '=': gradle reads a bare -DKEY as the empty string, "
                f"which Boolean.getBoolean reads as false - write {entry}=true, or {entry}= "
                f"for an empty value")
        match = _SYSTEM_PROPERTY.fullmatch(entry)
        if match is None:
            raise ValueError(
                f"-D '{entry}' is not KEY=VALUE with KEY a property name (a letter or '_', "
                f"then letters, digits, '_', '.' or '-'), written without the -D")
        bad = _CMD_META.search(match.group(2))
        if bad:
            raise ValueError(f"-D '{entry}' holds {bad.group()!r}, which the Windows "
                             f"wrapper's cmd.exe expands or splits on")
        flags.append(f"-D{entry}")
    return flags


def gradle_verify(
    module: str,
    tasks: list[str] | None = None,
    tail: int = 25,
    compile_only: bool = False,
    tests: list[str] | None = None,
    rerun: bool = False,
    rerun_tasks: bool = False,
    system_properties: list[str] | None = None,
    timeout: float = _TIMEOUT_SECONDS,
) -> dict:
    """Runs the module-scoped gradle gate and returns a structured verdict.

    Task options bind to the LAST task of the run, because gradle binds a task
    option to the task written immediately before it: the command line is
    ``<tasks> [--tests P]... [--rerun] -q --console=plain [--rerun-tasks]
    [-DKEY=VALUE]...``. With no task named they bind to `test`, the last of
    the default set. System properties carry no position and apply to the
    whole build.

    Args:
        module: module alias, name, or path (see toolsmith.modules).
        tasks: gradle tasks to run, in order. Defaults to compileJava+test, or
            compileJava+compileTestJava when compile_only is set; an empty
            list takes the defaults too.
        tail: how many signal (or de-noised trailing) lines to return.
        compile_only: use the compile-only default task set.
        tests: test filter patterns, each passed as --tests PATTERN to the last
            task. Refused under compile_only with no task named, since that
            default set has no test task.
        rerun: pass gradle's --rerun to the last task, so that task executes
            past its up-to-date check and the build cache (avoids FROM-CACHE -
            the tests actually execute) while the tasks before it may still be
            skipped. Note that clean does NOT do this: it only deletes build/,
            which the build cache immediately restores.
        rerun_tasks: pass --rerun-tasks, which forces EVERY task in the graph,
            compilation included, to execute.
        system_properties: JVM system properties for the build, each KEY=VALUE,
            passed as -DKEY=VALUE. A bare KEY is refused.
        timeout: seconds before the build is killed and the gate fails.

    Returns:
        dict with module, tasks, argv, root, exit_code, ok, first_failure,
        lines, lines_kind. argv is every argument gradle was given after the
        wrapper. On timeout, ok is False, exit_code is None, and timed_out is
        True. A refused argument returns ok False and an error, and runs
        nothing.

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
    named = bool(tasks)
    if not tasks:
        tasks = ["compileJava", "compileTestJava"] if compile_only else ["compileJava", "test"]

    # Refused before the module is resolved: a malformed argument is wrong
    # whichever module it was aimed at, and nothing has run yet.
    try:
        task_options = _task_options(tasks, named, compile_only, list(tests or ()), rerun)
        property_flags = _system_property_flags(list(system_properties or ()))
    except ValueError as refusal:
        return {"module": module, "ok": False, "error": str(refusal)}

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
    # flag. It forces every task in the graph past both up-to-date checks and
    # the build cache.
    flags = ["-q", "--console=plain"]
    if rerun_tasks:
        flags.append("--rerun-tasks")

    # The task options sit straight after the last task, which is the only
    # place gradle reads them as that task's. The base flags and the -D flags
    # follow them: gradle lifts an option it knows out of any position, so
    # they bind to nothing.
    argv = [*invoke, *task_options, *flags, *property_flags]
    command = ([gradlew, *argv] if os.name != "nt"
               else _windows_command_line([gradlew, *argv]))

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
                    command,
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
            "argv": argv,
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
        "argv": argv,
        "root": str(root),
        "exit_code": returncode,
        "ok": returncode == 0,
        "first_failure": signal[0] if signal else None,
        "lines": kept,
        "lines_kind": kind,
    }
