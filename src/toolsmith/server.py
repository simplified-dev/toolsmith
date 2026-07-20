"""FastMCP stdio server exposing the toolsmith tools.

This is a thin typed veneer: every tool forwards to the sibling library module
that does the real work. The point is to make the deterministic, high-frequency
operations a single cheap typed call instead of a re-derived shell incantation,
loaded ONLY under the Java workspace via a project-scoped .mcp.json.
"""
from __future__ import annotations

import contextlib
import io

from fastmcp import FastMCP

from . import gradle as _gradle
from . import imports as _imports
from . import javadoc as _javadoc
from . import modules as _modules
from . import tally as _tally

mcp = FastMCP("toolsmith")


@mcp.tool()
def gradle_verify(
    module: str,
    tasks: list[str] | None = None,
    tail: int = 25,
    compile_only: bool = False,
    rerun: bool = False,
) -> dict:
    """Run the module-scoped gradle gate and return a structured pass/fail result.

    Replaces the hand-written shape
    ``cd MODULE && ./gradlew compileJava test -q 2>&1 | grep -vE noise | tail -N``.

    Args:
        module: module alias (e.g. "ar"), name ("asset-renderer"), or path.
        tasks: gradle tasks to run. Defaults to compileJava+test.
        tail: how many signal / de-noised trailing lines to return.
        compile_only: use compileJava+compileTestJava as the default task set.
        rerun: force re-execution past up-to-date checks and the build cache
            (--rerun-tasks) so tests actually run instead of restoring
            FROM-CACHE. Note: clean does not do this.

    Returns:
        module, tasks, root, exit_code, ok, first_failure, lines, lines_kind.
        On timeout, ok is False, exit_code is None, and timed_out is True.

        Check lines_kind before reading anything into `lines`:
          "signal" - lines matched as failure diagnostics. Meaningful.
          "tail"   - no diagnostics matched, so this is just the last few
                     non-noise lines the build happened to print. It is NOT a
                     summary or a verdict, and on a passing run it is usually
                     unrelated chatter from whatever the build shelled out to.
          "empty"  - the build printed nothing after noise stripping. Normal
                     for a clean run under -q; not evidence the build no-opped.

        In particular `lines` is never a test tally. Counts printed there come
        from whatever the build spawned, not from gradle_verify - use test_tally
        or the JUnit XML for real numbers.
    """
    return _gradle.gradle_verify(module, tasks=tasks, tail=tail,
                                 compile_only=compile_only, rerun=rerun)


@mcp.tool()
def test_tally(module: str, subdir: str = "", fails: int = 15) -> dict:
    """Parse a module's JUnit XML and return counts plus the names of failing tests.

    Replaces the recurring grep/awk/python one-liners over
    build/test-results/test/*.xml.

    Args:
        module: module alias, name, or path whose test-results to tally.
        subdir: optional sub-path holding a nested build dir.
        fails: cap on the number of failing testcase names returned.
    """
    return _tally.tally(module, subdir=subdir, fails_cap=fails)


@mcp.tool()
def reorder_imports(paths: list[str], check: bool = False) -> dict:
    """Reorder Java imports to the IntelliJ Default layout (IDE-independent).

    Faithful to Optimize Imports: group 1 other, group 2 javax.* then java.*,
    group 3 static; ASCII sort; wildcards and CRLF/LF preserved; idempotent.

    Args:
        paths: .java files, directories, or globs.
        check: report what WOULD change without writing (a gate; sets no files).
    """
    return _imports.run(paths, mode="check" if check else "write")


@mcp.tool()
def javadoc_normalize(
    paths: list[str],
    fix: bool = False,
    scope: str = "all",
    prefix: list[str] | None = None,
) -> dict:
    """Audit (or --fix) Java javadocs against the project conventions.

    Args:
        paths: .java files, directories, or globs to process.
        fix: apply safe auto-fixes in place (otherwise audit-only).
        scope: one of class | method | field | all.
        prefix: extra FQN top-level prefixes to auto-import (additive).

    When fix is True, treat as destructive - re-Read any file you had already
    Read before editing it further.
    """
    argv: list[str] = ["--fix"] if fix else []
    argv += ["--scope", scope]
    for p in prefix or []:
        argv += ["--prefix", p]
    argv += list(paths)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = _javadoc.main(argv)
    return {"fixed": fix, "exit_code": code, "output": buf.getvalue()[-8000:]}


@mcp.tool()
def list_modules() -> dict:
    """List the workspace's discovered gradle modules.

    Each module: name, path (workspace-relative), package (base Java package),
    shorthand (short alias), buildable. Reads the cache written by `toolsmith
    setup`. Use this to look up a module's real base package or alias instead of
    guessing paths - several package roots do not match the directory name.
    """
    root = _modules.workspace_root()
    mods = _modules.get_modules()
    if not mods:
        return {"root": root.as_posix() if root else None, "modules": [],
                "note": "no inventory - run `toolsmith setup` in the workspace"}
    return {"root": root.as_posix(), "count": len(mods), "modules": mods}


def main() -> None:
    """Entry point: run the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
