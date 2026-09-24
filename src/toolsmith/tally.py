"""JUnit result tally for the Simplified modules.

Replaces the many hand-authored inline python/awk blocks that each re-parse
``build/test-results/<task>/*.xml`` for tests/skipped/failures/errors and list
the failing testcases. Counts come from the per-suite root attributes (reliable
and cheap); failing names come from the testcase elements.

A multi-project build keeps its results under each subproject rather than at
the root, so every ``build/test-results/<task>/`` under the module is read -
the root's own and each subproject's at any depth, for every test task.

Usable as a library (``tally``) and as a CLI (``python -m toolsmith.tally``).
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .modules import kind_of, resolve_module

# Directories holding tool state or build output rather than subprojects. A
# build directory is pruned as well: its own test-results are read from the
# directory above it, and nothing inside it is a project.
_PRUNED = frozenset({".git", ".gradle", "node_modules", "build"})

_COUNTS = ("classes", "tests", "passed", "skipped", "failures", "errors")


def results_dirs(base: Path) -> list[tuple[str, str, Path]]:
    """Finds every gradle test-results task directory under a build.

    Args:
        base: the build's root directory.

    Returns:
        one (project path, task name, results directory) per task directory,
        the project path posix and relative to base ("." for base itself),
        ordered root first, then by path and task.
    """
    found: list[tuple[str, str, Path]] = []
    for here, dirs, _ in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _PRUNED]
        results = Path(here) / "build" / "test-results"
        if not results.is_dir():
            continue
        rel = Path(here).relative_to(base).as_posix()
        found.extend((rel, task.name, task) for task in results.iterdir() if task.is_dir())
    return sorted(found, key=lambda row: (row[0] != ".", row[0], row[1]))


def _counts(xmls: list[Path], failing: list[str]) -> dict:
    """Sums the suite attributes of some JUnit files.

    Args:
        xmls: the JUnit files to read.
        failing: collects ``Class::test`` for each failing or erroring testcase.

    Returns:
        the counts, keyed as in ``_COUNTS``.
    """
    tests = skipped = failures = errors = 0
    for path in xmls:
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        tests += int(root.get("tests", 0))
        skipped += int(root.get("skipped", 0))
        failures += int(root.get("failures", 0))
        errors += int(root.get("errors", 0))
        cls = (root.get("name") or "").rsplit(".", 1)[-1]
        for case in root.findall("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                failing.append(f"{cls}::{case.get('name')}")
    return {
        "classes": len(xmls),
        "tests": tests,
        "passed": tests - skipped - failures - errors,
        "skipped": skipped,
        "failures": failures,
        "errors": errors,
    }


def tally_dir(mod_dir: Path, subdir: str = "", fails_cap: int = 15) -> dict:
    """Tallies the JUnit XML in every build/test-results/<task> under mod_dir/subdir.

    Args:
        mod_dir: the module directory.
        subdir: optional sub-path the search for results directories starts from.
        fails_cap: cap on the number of failing testcase names returned.

    Returns:
        dict with the grand total (``classes``, ``tests``, ``passed``,
        ``skipped``, ``failures``, ``errors``), ``found``, ``ok``,
        ``failing_tests`` / ``failing_total``, and ``results``: one row per
        results directory holding XML, carrying its ``path`` (posix, relative to
        the search root, "." for the root itself), its ``task`` and that
        directory's own counts.
    """
    base = mod_dir / subdir
    failing: list[str] = []
    rows: list[dict] = []
    for rel, task, directory in results_dirs(base):
        xmls = sorted(directory.glob("*.xml"))
        if xmls:
            rows.append({"path": rel, "task": task, **_counts(xmls, failing)})
    if not rows:
        return {"classes": 0, "found": False,
                "note": f"no result XML under any build/test-results in {base} (nothing ran)"}

    total = {key: sum(row[key] for row in rows) for key in _COUNTS}
    return {
        **total,
        "found": True,
        "ok": (total["failures"] + total["errors"]) == 0,
        "failing_tests": failing[:fails_cap],
        "failing_total": len(failing),
        "results": rows,
    }


def summary_line(counts: dict) -> str:
    """Formats the counts of a tally or of one of its results rows.

    Args:
        counts: a found tally result, or one row of its ``results``.

    Returns:
        the ``classes= tests= passed= skipped= failures= errors=`` line.
    """
    return " ".join(f"{key}={counts[key]}" for key in _COUNTS)


def breakdown_lines(result: dict) -> list[str]:
    """Formats one line per results directory, or none when there is only one.

    A module with a single results directory reads its whole answer off the
    total, so a breakdown there would only repeat it.

    Args:
        result: a found tally result.

    Returns:
        ``  <path>/<task>  <counts>`` per results directory.
    """
    rows = result.get("results", [])
    if len(rows) < 2:
        return []
    labels = [f"{row['path']}/{row['task']}" for row in rows]
    width = max(len(label) for label in labels)
    return [f"  {label:<{width}}  {summary_line(row)}" for label, row in zip(labels, rows)]


def tally(module: str, subdir: str = "", fails_cap: int = 15) -> dict:
    """Resolves a module token then tallies its test results.

    Args:
        module: module alias, name, or path.
        subdir: optional sub-path the search for results directories starts from.
        fails_cap: cap on the number of failing testcase names returned.

    Returns:
        dict with the module label plus the tally (see tally_dir).
    """
    mod_dir = resolve_module(module)
    if mod_dir is None:
        return {"module": module, "found": False, "note": f"module '{module}' not found"}
    # A JUnit XML directory a non-gradle project was never going to have reads as
    # "no tests" and says nothing about why. An unrecorded kind is not a wrong
    # one, so only a known non-gradle kind is refused.
    kind = kind_of(module)
    if kind is not None and kind != "gradle":
        return {"module": module, "found": False,
                "note": f"'{module}' is a {kind} project and writes no gradle test results"}
    return {"module": module, **tally_dir(mod_dir, subdir, fails_cap)}


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="toolsmith.tally",
                                 description="Tally JUnit results for a module.")
    ap.add_argument("module", nargs="?", default=".",
                    help="module alias / name / path (default: cwd)")
    ap.add_argument("--dir", default="", help="sub-path to search for results from")
    ap.add_argument("--fails", type=int, default=15, help="cap on failing names")
    args = ap.parse_args(argv)

    result = tally(args.module, args.dir, args.fails)
    if not result.get("found"):
        print(f"tally: {result.get('note', 'not found')}")
        return 2
    print(f"{summary_line(result)}   ({result['module']})")
    for line in breakdown_lines(result):
        print(line)
    for name in result["failing_tests"]:
        print(f"FAIL {name}")
    extra = result["failing_total"] - len(result["failing_tests"])
    if extra > 0:
        print(f"... (+{extra} more failing)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
