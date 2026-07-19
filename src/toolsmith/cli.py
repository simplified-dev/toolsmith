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
                             compile_only=args.compile_only)
    if r.get("error"):
        print(r["error"], file=sys.stderr)
        return 2
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
    return javadoc.main((["--fix"] if args.fix else []) + ["--scope", args.scope] + args.paths)


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
    s.set_defaults(func=_cmd_javadoc)

    s = sub.add_parser("locate", help="find a class file by name across module sources")
    s.add_argument("name")
    s.set_defaults(func=_cmd_locate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
