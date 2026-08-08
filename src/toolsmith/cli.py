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


def _print(text: str, file=sys.stdout) -> None:
    """Prints text a console encoding may not be able to represent.

    A build log is arbitrary third-party output and a Windows console is cp1252,
    which is enough for print to raise UnicodeEncodeError and take the whole log
    tail with it - at the one moment it is the only diagnostic there is. The
    check is done before printing rather than around it, so a stream that fails
    part-way cannot emit the line twice.
    """
    encoding = getattr(file, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        text = text.encode(encoding, "replace").decode(encoding, "replace")
    print(text, file=file)


def _jitpack_notes(r: dict) -> None:
    """Sends the diagnostic half of a jitpack result to stderr.

    Hints, log notes and the error line are commentary on the verdict rather
    than the verdict itself, so a piped stdout keeps only the facts and the
    final JITPACK: line.
    """
    if r.get("log_note"):
        _print(r["log_note"], file=sys.stderr)
    if r.get("error"):
        _print(r["error"], file=sys.stderr)
    for hint in r.get("hints") or []:
        _print(f"hint: {hint}", file=sys.stderr)


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

    if r.get("note"):
        print(r["note"], file=sys.stderr)

    if rc == 2:
        print("JITPACK: PRECONDITION")
    elif r["ok"]:
        print(f"JITPACK: OK {labels[0]} built" if len(entries) == 1
              else f"JITPACK: OK {len(entries)} refs built")
    elif not r.get("list_ok", True):
        # The list never answered, so no ref was scored. Inconclusive is its own
        # word: RED here would read as "this sha needs building".
        print(f"JITPACK: UNKNOWN {labels[0]} unreachable" if len(entries) == 1
              else f"JITPACK: UNKNOWN {len(entries)} refs unreachable")
    else:
        bad = next(i for i, e in enumerate(entries) if not e["ok"])
        print(f"JITPACK: RED {labels[bad]} {states[bad]}")
    return rc


def _jitpack_build(args: argparse.Namespace) -> int:
    from . import jitpack
    if len(args.ref) > 1:
        # One build is one ref: each distinct ref is a separate jitpack build.
        print("jitpack build takes at most one --ref", file=sys.stderr)
        return 2
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
    if r.get("precheck_error"):
        print(r["precheck_error"], file=sys.stderr)
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
        _print(r["log_tail"])
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
        print("JITPACK: OK 0 rows")
        return rc

    headers = ("artifact", "pin", "behind", "split", "jitpack", "consumers")
    cells = [(row["artifact"], row["pin"] or "-",
              "-" if row["behind"] is None else str(row["behind"]),
              # Only a real split is worth a number; 1 everywhere would be noise
              # in the column whose whole job is to make the splits findable.
              str(row["conflict"]) if row["conflict"] > 1 else "-",
              row["jitpack"], str(row["consumers"])) for row in rows]
    widths = [max(len(headers[i]), *(len(c[i]) for c in cells)) for i in range(len(headers))]
    # behind, split and consumers are counts, so they align right under their heading.
    numeric = (2, 3, 5)

    def render(values: tuple[str, ...]) -> str:
        return "  ".join(f"{v:>{widths[i]}}" if i in numeric else f"{v:<{widths[i]}}"
                         for i, v in enumerate(values)).rstrip()

    print(render(headers))
    for row in cells:
        print(render(row))

    # "N pins" was ambiguous between the two numbers and named the smaller: the
    # table has one line per distinct (artifact, pin) row, folded from a larger
    # number of declaration sites. Both are printed, each with its own word.
    median = "" if r["median_behind"] is None else f" | median behind={r['median_behind']}"
    tail = "".join([f" | {r['unreachable']} unreachable" if r["unreachable"] else "",
                    f" | {r['unpinned']} unpinned" if r["unpinned"] else ""])
    print(f"{r['total']} rows | {r['occurrences']} occurrences | {r['artifacts']} artifacts | "
          f"{r['stale']} stale | {r['unbuilt']} unbuilt | {r['errors']} error | "
          f"{r['conflicts']} split{tail}{median}")
    if r["conflicts"]:
        print(f"split across pins: {', '.join(r['conflicting'])}", file=sys.stderr)
        print(f"hint: {jitpack._CONFLICT_NOTE}", file=sys.stderr)
    if r.get("note"):
        print(r["note"], file=sys.stderr)

    if not r["ok"]:
        # Staleness alone is never red - most pins are stale, so a default-red
        # gate would be noise. What flips it is a pin jitpack cannot serve, or
        # an explicit --max-behind.
        reasons = [f"{r['unbuilt']} unbuilt"] if r["unbuilt"] else []
        if r["errors"]:
            reasons.append(f"{r['errors']} error")
        if r["unreachable"]:
            reasons.append(f"{r['unreachable']} unreachable")
        if r["over_max_behind"]:
            reasons.append(f"{r['over_max_behind']} over max-behind={r['max_behind']}")
        # Unreachable on its own is inconclusive, not red - nothing was scored.
        decided = r["unbuilt"] or r["errors"] or r["over_max_behind"]
        word = "RED" if decided else ("UNKNOWN" if r["unreachable"] else "RED")
        print(f"JITPACK: {word} {', '.join(reasons) or 'unresolvable pins'}")
    elif r["stale"]:
        print(f"JITPACK: STALE {r['stale']}/{r['total']} rows")
    else:
        print(f"JITPACK: OK {r['total']} rows")
    return rc


# One verdict word per jitpack set status, for the same reason build has one:
# "written" and "unchanged" are both exit 0 but are not the same outcome, and
# "unbuilt" and "unverified" are both exit 1 while meaning opposite things.
_SET_VERDICTS = {
    "written": "SET",
    "checked": "CHECK",
    "unchanged": "OK",
    "unbuilt": "RED",
    "unverified": "UNKNOWN",
    "write-failed": "FAILED",
    "precondition": "PRECONDITION",
}


def _jitpack_set(args: argparse.Namespace) -> int:
    from . import jitpack
    timeout = args.timeout if args.timeout is not None else jitpack.LIST_TIMEOUT
    r = jitpack.jitpack_set(args.artifact, args.sha, modules=args.module, check=args.check,
                            verify=not args.no_verify,
                            include_snapshots=args.include_snapshots, timeout=timeout)
    rc = jitpack.exit_code(r)
    status = r.get("status", "precondition")

    if status == "precondition":
        print(r["error"], file=sys.stderr)
        # The ids that DO exist, so a typo is answered in the same breath rather
        # than by a second command.
        if r.get("near"):
            print(f"closest: {', '.join(r['near'])}", file=sys.stderr)
        elif r.get("found"):
            print(f"pinned artifacts: {', '.join(r['found'])}", file=sys.stderr)
        for site in r.get("skipped_sites", []):
            print(f"skip {site['file']}:{site['line']}  {site['reason']}", file=sys.stderr)
        print("JITPACK: PRECONDITION")
        return rc

    print(f"artifact={r['artifact']} group={r.get('group', '-')} sha={r['sha']}")
    verification = r.get("verification")
    if verification is not None:
        print(f"verify={verification['state']} repo={verification.get('repo') or '-'} "
              f"list={_yn(verification.get('list_ok'))}")
    else:
        print("verify=skipped (--no-verify)", file=sys.stderr)

    if r["files"]:
        # "->" and "==" rather than a diff: one line per site is the whole
        # change, and the arrow is what separates a rewrite from a no-op.
        cells = [(f"{e['file']}:{e['line']}", e["pin"] or "-",
                  "->" if e["changed"] else "==", r["sha"], e["form"]) for e in r["files"]]
        widths = [max(len(c[i]) for c in cells) for i in range(4)]
        for site, before, arrow, after, form in cells:
            suffix = "" if form == "strictly" else f"  ({form})"
            print(f"{site:<{widths[0]}}  {before:>{widths[1]}} {arrow} "
                  f"{after:<{widths[2]}}{suffix}".rstrip())
    for site in r.get("skipped_sites", []):
        print(f"skip {site['file']}:{site['line']}  {site['reason']}", file=sys.stderr)

    verb = "would change" if r["check"] else "changed"
    print(f"{r['changed']} {verb} | {r['unchanged']} unchanged | {r['skipped']} skipped | "
          f"{r['files_touched']} file(s)")
    if r.get("note"):
        print(f"note: {r['note']}", file=sys.stderr)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
    word = _SET_VERDICTS.get(status, "FAILED")
    print(f"JITPACK: {word} {r['artifact']} {r['changed']}/{len(r['files'])} at {r['sha']}")
    return rc


def _jitpack_order(args: argparse.Namespace) -> int:
    from . import jitpack
    r = jitpack.jitpack_order(args.artifact)
    rc = jitpack.exit_code(r)
    if r.get("status") == "precondition":
        print(r["error"], file=sys.stderr)
        print("JITPACK: PRECONDITION")
        return rc

    # The arrow chain first: it is the shape this list was previously derived
    # in by hand, and it is the one line worth copying into a plan.
    print(" -> ".join(r["chain"]))
    headers = ("#", "depth", "artifact", "reason", "re-pin")
    cells = []
    for index, row in enumerate(r["order"], start=1):
        repins = [f"{p['artifact']}@{p['pin'] or '-'}" for p in row["repins"]]
        shown = ", ".join(repins[:3]) + (f" +{len(repins) - 3} more" if len(repins) > 3 else "")
        cells.append((str(index), str(row["depth"]), row["artifact"],
                      row["reason"], shown or "-"))
    widths = [max(len(headers[i]), *(len(c[i]) for c in cells)) for i in range(len(headers))]
    numeric = (0, 1)

    def render(values: tuple[str, ...]) -> str:
        return "  ".join(f"{v:>{widths[i]}}" if i in numeric else f"{v:<{widths[i]}}"
                         for i, v in enumerate(values)).rstrip()

    print(render(headers))
    for row in cells:
        print(render(row))

    print(f"{r['total']} to re-pin | {r['direct']} direct | {r['cascade']} cascade"
          + (f" | {len(r['floating'])} floating" if r["floating"] else "")
          + (f" | {len(r['cycles'])} in a cycle" if r["cycles"] else ""))
    if r["floating"]:
        print(f"floating (-SNAPSHOT, no edit needed): {', '.join(r['floating'])}",
              file=sys.stderr)
    if r["cycles"]:
        print(f"pin cycle - no valid order for: {', '.join(r['cycles'])}", file=sys.stderr)
    print(f"note: {r['note']}", file=sys.stderr)
    print(f"JITPACK: ORDER {r['total']} module(s) after {r['artifact']}")
    return rc


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

    # One parser per action rather than one shared flag set: --force and
    # --allow-symbolic only mean anything to build, and a shared parser accepted
    # them silently on status and pins, where they did nothing.
    s = sub.add_parser("jitpack", help="JitPack build status, trigger-and-wait, and pin drift",
                       description="Ask JitPack about a build, run one, or read and rewrite "
                                   "workspace pins.")
    actions = s.add_subparsers(dest="action", required=True,
                               metavar="{status,build,pins,set,order}")

    a = actions.add_parser("status", help="report whether refs are built; triggers nothing",
                           description="Reads one versionless list endpoint. Never starts a build, "
                                       "so it is the safe way to ask before pinning.")
    a.add_argument("target", help="module alias, name, or path")
    a.add_argument("--ref", action="append", default=[], metavar="REF",
                   help="git ref or short sha, repeatable (default: origin/<default branch>)")
    a.add_argument("--timeout", type=int, default=None,
                   help="seconds before the list read is abandoned (default: 20)")
    a.set_defaults(func=_jitpack_status)

    a = actions.add_parser("build", help="trigger and wait for ONE real jitpack build",
                           description="Starts a real build on a third-party service and blocks "
                                       "until it finishes. One invocation is one build.")
    a.add_argument("target", help="module alias, name, or path")
    a.add_argument("--ref", action="append", default=[], metavar="REF",
                   help="git ref or short sha, at most one (default: origin/<default branch>)")
    a.add_argument("--timeout", type=int, default=None,
                   help="seconds to hold the blocking request (default: 900)")
    a.add_argument("--log-lines", type=int, default=60,
                   help="trailing build.log lines to print on a failure (default: 60)")
    a.add_argument("--force", action="store_true",
                   help="re-request a sha the precheck already reported; cannot clear a "
                        "cached failure, only a new commit does")
    a.add_argument("--allow-symbolic", action="store_true",
                   help="permit a <branch>-SNAPSHOT ref instead of a sha")
    a.set_defaults(func=_jitpack_build)

    a = actions.add_parser("pins", help="audit every workspace pin for drift and splits",
                           description="One list read per artifact, read only. Never rewrites a "
                                       "coordinate and never requests a version.")
    a.add_argument("target", nargs="?", default=None, metavar="ARTIFACT",
                   help="case-insensitive artifact filter (default: every pin in the workspace)")
    a.add_argument("--timeout", type=int, default=None,
                   help="seconds before a list read is abandoned (default: 20)")
    a.add_argument("--max-behind", type=int, default=None, metavar="N",
                   help="fail when a pin is more than N commits behind its default branch")
    a.set_defaults(func=_jitpack_pins)

    a = actions.add_parser("set", help="rewrite an artifact's pin across the workspace",
                           description="The write counterpart of pins. Matches the artifact id "
                                       "EXACTLY, preserves each site's dialect, and errors "
                                       "rather than matching nothing quietly.")
    a.add_argument("artifact", help="artifact id, or <group>:<artifact> when one id is "
                                    "pinned under two groups")
    a.add_argument("sha", help="the sha to pin, cut to 7 chars; with verification on this "
                               "may be any ref git resolves, and the sha it resolves to is "
                               "what gets written")
    a.add_argument("--module", action="append", default=[], metavar="M",
                   help="narrow to this module, repeatable (default: every module that "
                        "pins the artifact)")
    a.add_argument("--check", action="store_true",
                   help="report what would change and write nothing")
    a.add_argument("--no-verify", action="store_true",
                   help="skip the jitpack check that the sha is pushed and built")
    a.add_argument("--include-snapshots", action="store_true",
                   help="also nail <branch>-SNAPSHOT coordinates to the sha; they float "
                        "onto the new commit by themselves otherwise")
    a.add_argument("--timeout", type=int, default=None,
                   help="seconds before the verification list read is abandoned (default: 20)")
    a.set_defaults(func=_jitpack_set)

    a = actions.add_parser("order", help="what has to be re-pinned after an artifact changes",
                           description="Walks the workspace pin graph. Read only, no network, "
                                       "and it distinguishes a pin that must move from one "
                                       "that moves by convention.")
    a.add_argument("artifact", help="the artifact whose sha is changing")
    a.set_defaults(func=_jitpack_order)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
