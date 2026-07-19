#!/usr/bin/env python3
"""simplified-tools - a tiny project-scoped MCP server for the Simplified Java workspace.

Exposes ONLY the deterministic, high-frequency operations that Claude currently
hand-authors over and over (the gradle noise-strip + PIPESTATUS shape, the
test-results XML tally) plus a typed front door to the existing javadoc normalizer.
It deliberately does NOT wrap the prompt-routing skills (bulk-rename, symbol-search,
find-usages, the auditors) - those stay skills so they cost nothing until invoked.

Register at W:/Workspace/Java/Simplified/.mcp.json so the tool schemas load ONLY when
working under that tree, not in every session.

Run:  python DRAFT-simplified-tools-mcp-server.py       (stdio)
Deps: pip install fastmcp
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from fastmcp import FastMCP

# Workspace root - the four family roots live directly under here.
WORKSPACE = Path(os.environ.get("SIMPLIFIED_ROOT", r"W:/Workspace/Java/Simplified"))

# Noise lines that every hand-written gradle invocation strips.
GRADLE_NOISE = re.compile(r"incubating|warning:|Deprecated|Daemon|Configure project")

mcp = FastMCP("simplified-tools")


def _find_module_dir(module: str) -> Path | None:
    """Locate a gradle module by name anywhere under the workspace (has a build file)."""
    for build in WORKSPACE.rglob("build.gradle*"):
        if build.parent.name == module:
            return build.parent
    return None


# ---------------------------------------------------------------------------
# TOOL 1: gradle_verify - kills the single most-repeated command shape.
# ---------------------------------------------------------------------------
@mcp.tool(
    annotations={
        "title": "Gradle verify (module-scoped)",
        "readOnlyHint": True,
        "openWorldHint": False,
    }
)
def gradle_verify(module: str, tasks: list[str] | None = None, tail: int = 40) -> dict:
    """Run the module-scoped gradle gate and return a structured pass/fail result.

    Replaces the hand-written shape:
        cd MODULE && ./gradlew compileJava test -q 2>&1 \
            | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS}"

    Args:
        module: gradle module name (e.g. "asset-renderer", "persistence").
        tasks: gradle tasks to run. Defaults to ["compileJava", "test"].
        tail: how many trailing noise-stripped output lines to return.

    Returns dict: {module, exit_code, ok, first_failure, tail_lines}.
    """
    tasks = tasks or ["compileJava", "test"]
    mod_dir = _find_module_dir(module)
    if mod_dir is None:
        return {"module": module, "ok": False, "error": f"module '{module}' not found under {WORKSPACE}"}

    root = mod_dir
    while root != root.parent and not (root / "gradlew").exists() and not (root / "gradlew.bat").exists():
        root = root.parent
    gradlew = str(root / ("gradlew.bat" if os.name == "nt" else "gradlew"))
    scoped = [f":{module}:{t}" for t in tasks]

    proc = subprocess.run(
        [gradlew, *scoped, "-q", "--console=plain"],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    merged = (proc.stdout + proc.stderr).splitlines()
    kept = [ln for ln in merged if ln.strip() and not GRADLE_NOISE.search(ln)]
    first_fail = next((ln for ln in kept if re.search(r"FAIL|error:|> Task .*FAILED|BUILD FAILED", ln)), None)
    return {
        "module": module,
        "tasks": scoped,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "first_failure": first_fail,
        "tail_lines": kept[-tail:],
    }


# ---------------------------------------------------------------------------
# TOOL 2: test_tally - kills the recurring JUnit XML tally.
# ---------------------------------------------------------------------------
@mcp.tool(
    annotations={
        "title": "JUnit result tally",
        "readOnlyHint": True,
        "openWorldHint": False,
    }
)
def test_tally(module: str) -> dict:
    """Parse a module's JUnit XML and return counts plus the names of failing tests.

    Replaces the recurring grep/awk/python one-liners over
    build/test-results/test/*.xml.

    Args:
        module: gradle module name whose test-results to tally.

    Returns dict: {module, total, passed, failed, errors, skipped, failing_tests[]}.
    """
    mod_dir = _find_module_dir(module)
    if mod_dir is None:
        return {"module": module, "error": f"module '{module}' not found under {WORKSPACE}"}

    totals = {"total": 0, "failed": 0, "errors": 0, "skipped": 0}
    failing: list[str] = []
    for xml in mod_dir.rglob("build/test-results/test/*.xml"):
        try:
            root = ET.parse(xml).getroot()
        except ET.ParseError:
            continue
        for case in root.iter("testcase"):
            totals["total"] += 1
            name = f"{case.get('classname', '?')}.{case.get('name', '?')}"
            if case.find("failure") is not None:
                totals["failed"] += 1
                failing.append(name)
            elif case.find("error") is not None:
                totals["errors"] += 1
                failing.append(name)
            elif case.find("skipped") is not None:
                totals["skipped"] += 1
    totals["passed"] = totals["total"] - totals["failed"] - totals["errors"] - totals["skipped"]
    return {"module": module, **totals, "failing_tests": failing[:50]}


# ---------------------------------------------------------------------------
# TOOL 3: javadoc_normalize - typed front door to the existing normalize.py.
# ---------------------------------------------------------------------------
NORMALIZE_PY = Path.home() / ".claude" / "skills" / "javadoc-normalize" / "normalize.py"


@mcp.tool(
    annotations={
        "title": "Javadoc normalize",
        "readOnlyHint": False,   # audit is read-only, but fix=True mutates files
        "destructiveHint": False,
        "openWorldHint": False,
    }
)
def javadoc_normalize(
    paths: list[str],
    fix: bool = False,
    scope: str = "all",
    prefix: list[str] | None = None,
) -> dict:
    """Audit (or --fix) Java javadocs via the existing normalize.py.

    Args:
        paths: .java files, directories, or globs to process.
        fix: apply safe auto-fixes in place (otherwise audit-only).
        scope: one of class | method | field | all.
        prefix: extra FQN top-level prefixes to auto-import (additive).

    Returns dict: {fixed, exit_code, output}. When fix is True, treat as
    destructive - re-Read any file you had already Read before editing further.
    """
    if not NORMALIZE_PY.exists():
        return {"error": f"normalize.py not found at {NORMALIZE_PY}"}
    argv = [sys.executable, str(NORMALIZE_PY)]
    if fix:
        argv.append("--fix")
    argv += ["--scope", scope]
    for p in prefix or []:
        argv += ["--prefix", p]
    argv += list(paths)
    proc = subprocess.run(argv, capture_output=True, text=True)
    return {"fixed": fix, "exit_code": proc.returncode, "output": (proc.stdout + proc.stderr)[-8000:]}


if __name__ == "__main__":
    mcp.run()  # stdio transport
