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

from .modules import WORKSPACE, find_gradle_root, resolve_module

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
) -> dict:
    """Runs the module-scoped gradle gate and returns a structured verdict.

    Args:
        module: module alias, name, or path (see toolsmith.modules).
        tasks: gradle tasks to run. Defaults to compileJava+test, or
            compileJava+compileTestJava when compile_only is set.
        tail: how many signal (or de-noised trailing) lines to return.
        compile_only: use the compile-only default task set.

    Returns:
        dict with module, tasks, root, exit_code, ok, first_failure, lines.
    """
    if tasks is None:
        tasks = ["compileJava", "compileTestJava"] if compile_only else ["compileJava", "test"]

    mod_dir = resolve_module(module)
    if mod_dir is None:
        return {"module": module, "ok": False,
                "error": f"module '{module}' not found under {WORKSPACE}"}

    root = find_gradle_root(mod_dir)
    if root is None:
        return {"module": module, "ok": False,
                "error": f"no gradle wrapper found upward from {mod_dir}"}

    # A standalone repo IS the root project -> unscoped tasks. Only a genuine
    # subproject (root above the module dir) needs the ':module:task' form.
    invoke = tasks if root == mod_dir else [f":{module}:{t}" for t in tasks]
    gradlew = str(root / ("gradlew.bat" if os.name == "nt" else "gradlew"))

    proc = subprocess.run(
        [gradlew, *invoke, "-q", "--console=plain"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    lines = (proc.stdout + proc.stderr).splitlines()
    signal = [ln for ln in lines if _SIGNAL.search(ln) and not _NOISE.search(ln)]
    if signal:
        kept = signal[:tail]
    else:
        kept = [ln for ln in lines if ln.strip() and not _NOISE.search(ln)][-tail:]

    return {
        "module": module,
        "tasks": invoke,
        "root": str(root),
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "first_failure": signal[0] if signal else None,
        "lines": kept,
    }
