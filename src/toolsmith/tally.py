"""JUnit result tally for the Simplified modules.

Replaces the many hand-authored inline python/awk blocks that each re-parse
``build/test-results/test/*.xml`` for tests/skipped/failures/errors and list the
failing testcases. Counts come from the per-suite root attributes (reliable and
cheap); failing names come from the testcase elements.

Usable as a library (``tally``) and as a CLI (``python -m toolsmith.tally``).
"""
from __future__ import annotations

import glob
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .modules import kind_of, resolve_module


def tally_dir(mod_dir: Path, subdir: str = "", fails_cap: int = 15) -> dict:
    """Tallies the JUnit XML under mod_dir/subdir/build/test-results/test."""
    results = (mod_dir / subdir / "build" / "test-results" / "test")
    xmls = sorted(glob.glob(str(results).replace("\\", "/") + "/*.xml"))
    if not xmls:
        return {"classes": 0, "found": False,
                "note": f"no result XML under {results} (nothing ran)"}

    tests = skipped = failures = errors = 0
    failing: list[str] = []
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
        "found": True,
        "tests": tests,
        "passed": tests - skipped - failures - errors,
        "skipped": skipped,
        "failures": failures,
        "errors": errors,
        "ok": (failures + errors) == 0,
        "failing_tests": failing[:fails_cap],
        "failing_total": len(failing),
    }


def tally(module: str, subdir: str = "", fails_cap: int = 15) -> dict:
    """Resolves a module token then tallies its test results.

    Args:
        module: module alias, name, or path.
        subdir: optional sub-path holding a nested build dir.
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
    ap.add_argument("--dir", default="", help="sub-path holding the build dir")
    ap.add_argument("--fails", type=int, default=15, help="cap on failing names")
    args = ap.parse_args(argv)

    result = tally(args.module, args.dir, args.fails)
    if not result.get("found"):
        print(f"tally: {result.get('note', 'not found')}")
        return 2
    print(f"classes={result['classes']} tests={result['tests']} "
          f"passed={result['passed']} skipped={result['skipped']} "
          f"failures={result['failures']} errors={result['errors']}   ({result['module']})")
    for name in result["failing_tests"]:
        print(f"FAIL {name}")
    extra = result["failing_total"] - len(result["failing_tests"])
    if extra > 0:
        print(f"... (+{extra} more failing)")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(_main())
