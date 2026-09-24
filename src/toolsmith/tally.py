"""JUnit result tally for the Simplified modules.

Replaces the many hand-authored inline python/awk blocks that each re-parse
``build/test-results/<task>/*.xml`` for tests/skipped/failures/errors and list
the failing testcases. Counts come from the per-suite root attributes (reliable
and cheap); failing names come from the testcase elements.

A multi-project build keeps its results under each subproject rather than at
the root, so every ``build/test-results/<task>/`` under the module is read -
the root's own and each subproject's at any depth, for every test task.

A results directory whose newest XML is older than the newest compiled test
class of its subproject is stale - an earlier run left it behind, and the
classes have been rebuilt since. It is still listed, with its age, but left
out of the total. The classes of the same name (``build/classes/<language>/
<task>/``) are what it is judged by where they exist; a task that runs another
source set's classes has none of its own, so it is judged by the newest test
class anywhere under the subproject's ``build/classes``. A directory with no
compiled classes to compare against is never stale.

Usable as a library (``tally``) and as a CLI (``python -m toolsmith.tally``).
"""
from __future__ import annotations

import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from .modules import kind_of, resolve_module

# Directories holding tool state or build output rather than subprojects. A
# build directory is pruned as well: its own test-results are read from the
# directory above it, and nothing inside it is a project.
_PRUNED = frozenset({".git", ".gradle", "node_modules", "build"})

_COUNTS = ("classes", "tests", "passed", "skipped", "failures", "errors")

# The production source set: its classes are not test classes, so they never
# decide whether a results directory is stale.
_MAIN_SOURCE_SET = "main"


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


def newest_test_class(project: Path, task: str) -> float | None:
    """Finds when the newest compiled test class a test task could have run was written.

    Args:
        project: the (sub)project directory holding the ``build`` directory.
        task: the test task, named as its ``build/test-results`` directory is.

    Returns:
        the modification time of the newest ``.class`` under
        ``build/classes/<language>/<task>/`` where such a directory exists, and
        otherwise under every non-``main`` source set of ``build/classes``;
        None when there is no compiled class to compare against.
    """
    classes = project / "build" / "classes"
    if not classes.is_dir():
        return None
    languages = [lang for lang in classes.iterdir() if lang.is_dir()]
    own = [lang / task for lang in languages if (lang / task).is_dir()]
    source_sets = own or [s for lang in languages for s in lang.iterdir()
                          if s.is_dir() and s.name != _MAIN_SOURCE_SET]
    return max((path.stat().st_mtime for s in source_sets for path in s.rglob("*.class")),
               default=None)


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
        the search root, "." for the root itself), its ``task``, that
        directory's own counts, ``stale`` and ``age_seconds`` (whole seconds
        since its newest XML was written). A stale row's counts and failing
        tests stay out of the total, ``ok`` and ``failing_tests``.
    """
    base = mod_dir / subdir
    now = time.time()
    failing: list[str] = []
    rows: list[dict] = []
    for rel, task, directory in results_dirs(base):
        xmls = sorted(directory.glob("*.xml"))
        if not xmls:
            continue
        written = max(path.stat().st_mtime for path in xmls)
        compiled = newest_test_class(base / rel, task)
        stale = compiled is not None and written < compiled
        counts = _counts(xmls, [] if stale else failing)
        rows.append({"path": rel, "task": task, **counts,
                     "stale": stale, "age_seconds": max(0, int(now - written))})
    if not rows:
        return {"classes": 0, "found": False,
                "note": f"no result XML under any build/test-results in {base} (nothing ran)"}

    total = {key: sum(row[key] for row in rows if not row["stale"]) for key in _COUNTS}
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


def age_text(seconds: int) -> str:
    """Formats an age in seconds as its two largest units.

    Args:
        seconds: the age, in whole seconds.

    Returns:
        ``<d>d <h>h``, ``<h>h <m>m``, ``<m>m`` or ``<s>s``.
    """
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else f"{secs}s"


def breakdown_lines(result: dict) -> list[str]:
    """Formats one line per results directory, or none when there is only one fresh one.

    A module with a single results directory reads its whole answer off the
    total, so a breakdown there would only repeat it - unless that directory is
    stale, when the total leaves it out and only its row says where it went.

    Args:
        result: a found tally result.

    Returns:
        ``  <path>/<task>  <counts>`` per results directory, a stale one
        followed by its age and that it is not in the total.
    """
    rows = result.get("results", [])
    if len(rows) < 2 and not any(row["stale"] for row in rows):
        return []
    labels = [f"{row['path']}/{row['task']}" for row in rows]
    width = max(len(label) for label in labels)
    lines = []
    for label, row in zip(labels, rows):
        line = f"  {label:<{width}}  {summary_line(row)}"
        if row["stale"]:
            line += f"  stale, {age_text(row['age_seconds'])} old - not in the total"
        lines.append(line)
    return lines


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
