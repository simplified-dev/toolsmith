"""toolsmith command-line interface.

One installable entry point for the whole toolset: run the MCP server (`serve`),
discover a workspace (`setup`), and invoke each tool directly from the shell -
folding the former gw / jtally / locate-java helpers into subcommands.
"""
from __future__ import annotations

import argparse
import sys

from . import discovery, gradle, modules
from . import imports as imports_mod
from . import tally as tally_mod


def _cmd_setup(args: argparse.Namespace) -> int:
    root, mods = discovery.run_setup(args.root)
    buildable = sum(1 for m in mods if m["buildable"])
    print(f"toolsmith: scanned {root}")
    print(f"  {len(mods)} gradle module(s), {buildable} buildable")
    width = max((len(m["shorthand"]) for m in mods), default=2)
    for m in mods:
        print(f"  {m['shorthand']:<{width}}  {m['path']:<44}  {m['package'] or '-'}")
    print(f"cache: {root}/.toolsmith/modules.json")
    return 0


def _cmd_modules(args: argparse.Namespace) -> int:
    mods = modules.get_modules()
    if not mods:
        print("no inventory - run `toolsmith setup [ROOT]` first", file=sys.stderr)
        return 2
    width = max((len(m["shorthand"]) for m in mods), default=2)
    for m in mods:
        print(f"{m['shorthand']:<{width}}  {m['name']:<26}  {m['package'] or '-'}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .server import mcp
    mcp.run()
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    r = gradle.gradle_verify(args.module, tasks=args.tasks or None, tail=args.tail,
                             compile_only=args.compile_only, rerun=args.rerun)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return 2
    # Flag a tail so it is not read as a summary - it is only the last few
    # lines the build happened to print, often from something it shelled out
    # to. On stderr so a piped stdout stays clean. See gradle_verify's docstring.
    if r["lines"] and r.get("lines_kind") == "tail":
        print("-- trailing build output, not a summary --", file=sys.stderr)
    for line in r["lines"]:
        print(line)
    print(f"GATE: {'PASS' if r['ok'] else 'FAIL'} rc={r['exit_code']}")
    return 0 if r["ok"] else 1


def _cmd_tally(args: argparse.Namespace) -> int:
    r = tally_mod.tally(args.module, fails_cap=args.fails)
    if not r.get("found"):
        print(f"tally: {r.get('note', 'not found')}")
        return 2
    print(f"classes={r['classes']} tests={r['tests']} passed={r['passed']} "
          f"skipped={r['skipped']} failures={r['failures']} errors={r['errors']}  ({r['module']})")
    for name in r["failing_tests"]:
        print(f"FAIL {name}")
    return 0 if r["ok"] else 1


def _cmd_reorder(args: argparse.Namespace) -> int:
    return imports_mod.main((["--check"] if args.check else []) + args.paths)


def _cmd_javadoc(args: argparse.Namespace) -> int:
    from . import javadoc
    argv = (["--fix"] if args.fix else []) + ["--scope", args.scope]
    for prefix in args.prefix:
        argv += ["--prefix", prefix]
    return javadoc.main(argv + args.paths)


def _cmd_locate(args: argparse.Namespace) -> int:
    root, mods = modules.workspace_root(), modules.get_modules()
    if not mods or root is None:
        print("no inventory - run `toolsmith setup` first", file=sys.stderr)
        return 2
    fname = args.name if args.name.endswith(".java") else f"{args.name}.java"
    hits = []
    for m in mods:
        if not m["buildable"]:
            continue
        src = root / m["path"] / "src"
        if src.is_dir():
            hits.extend(src.rglob(fname))
    for path in hits:
        print(path.as_posix())
    if not hits:
        print(f"no {fname} under any module src", file=sys.stderr)
        return 1
    return 0


# One verdict word per jitpack build status. The status set is deliberately
# wider than the 0/1/2 exit codes - a timeout and a real failure are both exit 1
# but mean opposite things - so the word carries what the code cannot.
_JITPACK_VERDICTS = {
    "built": "BUILT",
    "already-built": "OK",
    "in-flight": "BUILDING",
    "timeout": "TIMEOUT",
    "failed": "FAILED",
    "cached-failure": "FAILED",
    "error": "ERROR",
    "symbolic": "PRECONDITION",
    "precondition": "PRECONDITION",
}


def _yn(value: object) -> str:
    return "yes" if value else "no"


def _jitpack_notes(r: dict) -> None:
    """Sends the diagnostic half of a jitpack result to stderr.

    Hints, log notes and the error line are commentary on the verdict rather
    than the verdict itself, so a piped stdout keeps only the facts and the
    final JITPACK: line.
    """
    if r.get("log_note"):
        print(r["log_note"], file=sys.stderr)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
    for hint in r.get("hints") or []:
        print(f"hint: {hint}", file=sys.stderr)


def _jitpack_verdict(r: dict) -> str:
    """Renders the trailing verdict of a build, mirroring `GATE: PASS rc=0`."""
    status = r.get("status") or "precondition"
    word = _JITPACK_VERDICTS.get(status, "FAILED")
    subject = r.get("ref") or r.get("source") or r.get("module") or "?"
    if status == "built":
        return f"{word} {subject} in {r.get('elapsed', 0.0)}s"
    if status == "already-built":
        return f"{word} {subject} already built"
    if status == "timeout":
        return f"{word} {subject} after {r.get('elapsed', 0.0)}s - re-run to attach"
    if word == "PRECONDITION":
        return f"{word} {subject}"
    return f"{word} {subject} ({status})"


def _jitpack_status(args: argparse.Namespace) -> int:
    from . import jitpack
    timeout = args.timeout if args.timeout is not None else jitpack.LIST_TIMEOUT
    r = jitpack.jitpack_status(args.target, refs=args.ref, timeout=timeout)
    rc = jitpack.exit_code(r)
    if not r.get("group"):
        print(r.get("error", f"jitpack status failed for '{args.target}'"), file=sys.stderr)
        return rc

    print(f"group={r['group']} artifact={r['artifact']} repo={r['repo']}")
    entries = r["refs"]
    if len(entries) == 1 and not entries[0].get("error"):
        e = entries[0]
        print(f"ref={e['ref']} source={e['source']} pushed={_yn(e['pushed'])} "
              f"unambiguous={_yn(e['unambiguous'])}")
    counts = r["counts"]
    print(f"records={r['records']} ok={counts['ok']} error={counts['error']} "
          f"building={counts['in-flight']}")

    labels = [e.get("ref") or e.get("source") or "?" for e in entries]
    states = [e.get("status") or e.get("state") or "?" for e in entries]
    label_w = max(len(x) for x in labels)
    state_w = max(len(x) for x in states)
    for entry, label, state in zip(entries, labels, states):
        source = entry.get("source")
        arrow = f"  <- {source}" if source and source != label else ""
        print(f"{label:<{label_w}}  {state:<{state_w}}{arrow}".rstrip())
    for entry in entries:
        if entry.get("error"):
            print(entry["error"], file=sys.stderr)

    if rc == 2:
        print("JITPACK: PRECONDITION")
    elif r["ok"]:
        print(f"JITPACK: OK {labels[0]} built" if len(entries) == 1
              else f"JITPACK: OK {len(entries)} refs built")
    else:
        bad = next(i for i, e in enumerate(entries) if not e["ok"])
        print(f"JITPACK: RED {labels[bad]} {states[bad]}")
    return rc


def _jitpack_build(args: argparse.Namespace) -> int:
    from . import jitpack
    timeout = args.timeout if args.timeout is not None else jitpack.BUILD_TIMEOUT
    # The watchdog line is cosmetic and lands while the blocking request is
    # still open - i.e. ahead of every result line below. Keeping it on stderr
    # is what lets a piped stdout stay in the documented order.
    r = jitpack.jitpack_build(
        args.target,
        ref=args.ref[0] if args.ref else None,
        timeout=timeout,
        force=args.force,
        allow_symbolic=args.allow_symbolic,
        log_lines=args.log_lines,
        progress=lambda line: print(line, file=sys.stderr, flush=True),
    )
    rc = jitpack.exit_code(r)
    if not r.get("group"):
        print(r.get("error", f"jitpack build failed for '{args.target}'"), file=sys.stderr)
        return rc

    print(f"group={r['group']} artifact={r['artifact']} repo={r['repo']}")
    if r.get("ref"):
        print(f"ref={r['ref']} full={r.get('full_sha') or '-'} source={r.get('source')} "
              f"symbolic={_yn(r.get('symbolic'))}")
    if r.get("precheck"):
        print(f"precheck={r['precheck']} action={r.get('action', 'none')} timeout={timeout:g}s")
    if r.get("resolved_version"):
        print(f"resolved={r['resolved_version']} location={r.get('location') or '-'}")
    if r.get("status") != "precondition":
        line = [f"built={_yn(r.get('ok'))}", f"http={r.get('http_code') or '-'}",
                f"elapsed={r.get('elapsed', 0.0)}s"]
        if r.get("bytes") is not None:
            line.append(f"bytes={r['bytes']}")
        line.append(f"status={r['status']}")
        print(" ".join(line))
    if r.get("note"):
        print(f"note: {r['note']}")
    if r.get("pin"):
        print(f"pin: {r['pin']}")
    if r.get("log_tail"):
        # Label the tail so it is not read as a summary, as _cmd_verify does.
        print(f"-- build.log tail: {r.get('log_url') or ''} --", file=sys.stderr)
        print(r["log_tail"])
    _jitpack_notes(r)
    print(f"JITPACK: {_jitpack_verdict(r)}")
    return rc


def _jitpack_pins(args: argparse.Namespace) -> int:
    from . import jitpack
    timeout = args.timeout if args.timeout is not None else jitpack.LIST_TIMEOUT
    r = jitpack.jitpack_pins(args.target, max_behind=args.max_behind, timeout=timeout)
    rc = jitpack.exit_code(r)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return rc

    rows = r["pins"]
    if not rows:
        print(r.get("note", "no jitpack pins found"), file=sys.stderr)
        print("JITPACK: OK 0 pins")
        return rc

    headers = ("artifact", "pin", "behind", "jitpack", "consumers")
    cells = [(row["artifact"], row["pin"] or "-",
              "-" if row["behind"] is None else str(row["behind"]),
              row["jitpack"], str(row["consumers"])) for row in rows]
    widths = [max(len(headers[i]), *(len(c[i]) for c in cells)) for i in range(len(headers))]
    # behind and consumers are counts, so they align right under their heading.
    numeric = (2, 4)

    def render(values: tuple[str, ...]) -> str:
        return "  ".join(f"{v:>{widths[i]}}" if i in numeric else f"{v:<{widths[i]}}"
                         for i, v in enumerate(values)).rstrip()

    print(render(headers))
    for row in cells:
        print(render(row))
    median = "" if r["median_behind"] is None else f" | median behind={r['median_behind']}"
    print(f"{r['total']} pins | {r['artifacts']} artifacts | {r['stale']} stale | "
          f"{r['unbuilt']} unbuilt | {r['errors']} error{median}")

    if not r["ok"]:
        # Staleness alone is never red - most pins are stale, so a default-red
        # gate would be noise. What flips it is a pin jitpack cannot serve, or
        # an explicit --max-behind.
        reasons = [f"{r['unbuilt']} unbuilt"] if r["unbuilt"] else []
        if r["errors"]:
            reasons.append(f"{r['errors']} error")
        if r["over_max_behind"]:
            reasons.append(f"{r['over_max_behind']} over max-behind={r['max_behind']}")
        print(f"JITPACK: RED {', '.join(reasons) or 'unresolvable pins'}")
    elif r["stale"]:
        print(f"JITPACK: STALE {r['stale']}/{r['total']}")
    else:
        print(f"JITPACK: OK {r['total']} pins")
    return rc


def _cmd_jitpack(args: argparse.Namespace) -> int:
    if args.action == "pins":
        return _jitpack_pins(args)
    if not args.target:
        print(f"jitpack {args.action} needs a module (alias, name, or path)", file=sys.stderr)
        return 2
    if args.action == "status":
        return _jitpack_status(args)
    if len(args.ref) > 1:
        # One build is one ref: each distinct ref is a separate jitpack build.
        print("jitpack build takes at most one --ref", file=sys.stderr)
        return 2
    return _jitpack_build(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="toolsmith",
                                     description="Java workspace dev tools + MCP server.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="scan a workspace and cache its module inventory")
    s.add_argument("root", nargs="?", default=".", help="workspace root to scan (default: cwd)")
    s.set_defaults(func=_cmd_setup)

    sub.add_parser("serve", help="run the stdio MCP server").set_defaults(func=_cmd_serve)
    sub.add_parser("modules", help="print the cached module inventory").set_defaults(func=_cmd_modules)

    s = sub.add_parser("verify", help="module-scoped gradle gate")
    s.add_argument("module")
    s.add_argument("tasks", nargs="*")
    s.add_argument("--tail", type=int, default=25)
    s.add_argument("--compile-only", action="store_true")
    s.add_argument("--rerun", action="store_true",
                   help="force re-execution past up-to-date + build cache (--rerun-tasks)")
    s.set_defaults(func=_cmd_verify)

    s = sub.add_parser("tally", help="JUnit result tally")
    s.add_argument("module")
    s.add_argument("--fails", type=int, default=15)
    s.set_defaults(func=_cmd_tally)

    s = sub.add_parser("reorder", help="reorder Java imports to the IntelliJ Default layout")
    s.add_argument("paths", nargs="+")
    s.add_argument("--check", action="store_true")
    s.set_defaults(func=_cmd_reorder)

    s = sub.add_parser("javadoc", help="audit or --fix javadocs")
    s.add_argument("paths", nargs="+")
    s.add_argument("--fix", action="store_true")
    s.add_argument("--scope", default="all", choices=["class", "method", "field", "all"])
    s.add_argument("--prefix", action="append", default=[], help="extra FQN top-level prefix (repeatable)")
    s.set_defaults(func=_cmd_javadoc)

    s = sub.add_parser("locate", help="find a class file by name across module sources")
    s.add_argument("name")
    s.set_defaults(func=_cmd_locate)

    s = sub.add_parser("jitpack", help="JitPack build status, trigger-and-wait, and pin drift")
    s.add_argument("action", choices=["status", "build", "pins"])
    s.add_argument("target", nargs="?", default=None,
                   help="module alias/name/path (status, build); artifact filter (pins)")
    s.add_argument("--ref", action="append", default=[],
                   help="git ref or short sha; repeatable for status, at most one for build. "
                        "Default: origin/<default branch>")
    s.add_argument("--timeout", type=int, default=None,
                   help="seconds to wait (default: 900 for build, 20 for status and pins)")
    s.add_argument("--log-lines", type=int, default=60)
    s.add_argument("--max-behind", type=int, default=None,
                   help="pins: fail when a pin is more than N commits behind its default branch")
    s.add_argument("--force", action="store_true",
                   help="build: re-request a sha the precheck already reported")
    s.add_argument("--allow-symbolic", action="store_true",
                   help="build: permit a <branch>-SNAPSHOT ref instead of a sha")
    s.set_defaults(func=_cmd_jitpack)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
