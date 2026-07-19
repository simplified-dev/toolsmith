"""Reorder Java imports to match the IntelliJ built-in Default import layout.

This is the IDE-independent fallback used when the IntelliJ MCP is not attached.
It reproduces - byte for byte - what Optimize Imports produces under the Default
scheme (the authoritative scheme for this codebase, which ships no custom
IMPORT_LAYOUT_TABLE and no .editorconfig). Derived empirically from committed
source across all four family roots. The layout is:

    <all other non-static imports, ASCII-sorted>       # group 1
    <blank line>
    <javax.* ASCII-sorted, then java.* ASCII-sorted>   # group 2
    <blank line>
    <all static imports, ASCII-sorted>                 # group 3

Rules that make this faithful and NOT reproducible by a naive line sort:
  - Group 2 is NOT alphabetical: javax.* precedes java.* even though a flat
    string sort ranks "java." before "javax" ('.'=46 < 'x'=120).
  - Only the exact top segments `java` and `javax` are special-cased; jakarta.*,
    io.*, net.*, reactor.*, etc. are "all other" (group 1).
  - Sorting is flat-string ASCII (Java String.compareTo == Python str compare):
    an upper-cased class segment sorts before a lower-cased sub-package at the
    same depth. Case-insensitive sorts get this wrong.
  - Static imports form ONE trailing group regardless of package.
  - Existing wildcards are preserved verbatim - never expanded or collapsed.

Safety: CRLF/LF and trailing-newline state are preserved; only the contiguous
import region is rewritten; a file whose import block interleaves a non-import,
non-blank line (e.g. a comment) is skipped rather than risk dropping it;
idempotent.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

IMPORT_RE = re.compile(
    r"^import(?P<static>\s+static)?\s+(?P<path>[\w.]+(?:\.\*)?)\s*;[ \t]*$"
)

EXCLUDED_DIR_PARTS = {
    "build", ".gradle", ".idea", ".intellijPlatform", "out", "target",
    "node_modules", ".git", "bin", "classes", "generated", "generated-sources",
}


def _top_segment(path: str) -> str:
    return path.split(".", 1)[0]


def reorder_text(text_lf: str) -> tuple[str, bool, str | None]:
    """Reorders the import block of LF-normalized source.

    Returns (new_text, changed, skip_reason). skip_reason is non-None when the
    file was deliberately left untouched for a safety reason (changed=False).
    """
    lines = text_lf.split("\n")

    idx = [i for i, ln in enumerate(lines) if IMPORT_RE.match(ln)]
    if not idx:
        return text_lf, False, None
    first, last = idx[0], idx[-1]

    for i in range(first, last + 1):
        stripped = lines[i].strip()
        if stripped and not IMPORT_RE.match(lines[i]):
            return text_lf, False, "non-import line inside import block"

    javax_b: list[str] = []
    java_b: list[str] = []
    other_b: list[str] = []
    static_b: list[str] = []
    for i in range(first, last + 1):
        m = IMPORT_RE.match(lines[i])
        if not m:
            continue
        path = m.group("path")
        if m.group("static"):
            static_b.append(f"import static {path};")
        else:
            seg = _top_segment(path)
            line = f"import {path};"
            if seg == "javax":
                javax_b.append(line)
            elif seg == "java":
                java_b.append(line)
            else:
                other_b.append(line)

    def _sorted_unique(block: list[str], key_start: int) -> list[str]:
        return sorted(dict.fromkeys(block), key=lambda s: s[key_start:])

    other_b = _sorted_unique(other_b, len("import "))
    javax_b = _sorted_unique(javax_b, len("import "))
    java_b = _sorted_unique(java_b, len("import "))
    static_b = _sorted_unique(static_b, len("import static "))

    groups: list[list[str]] = []
    if other_b:
        groups.append(other_b)
    if javax_b or java_b:
        groups.append(javax_b + java_b)
    if static_b:
        groups.append(static_b)

    rebuilt = "\n\n".join("\n".join(g) for g in groups)
    new_lines = lines[:first] + rebuilt.split("\n") + lines[last + 1:]
    new_text = "\n".join(new_lines)
    return new_text, new_text != text_lf, None


def process_file(path: Path, mode: str) -> tuple[str, str | None]:
    """Handles one file. mode in {write, check, diff}.

    Returns (status, detail); status in
    {unchanged, changed, would-change, skipped, error}.
    """
    try:
        raw = path.read_bytes()
    except (PermissionError, OSError) as exc:
        return "error", str(exc)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "error", "not utf-8"
    had_final_nl = text.endswith("\n")
    text_lf = text.replace("\r\n", "\n")
    body = text_lf[:-1] if text_lf.endswith("\n") else text_lf

    new_body, changed, skip = reorder_text(body)
    if skip:
        return "skipped", skip
    if not changed:
        return "unchanged", None

    if mode == "diff":
        a = body.splitlines(keepends=True)
        b = new_body.splitlines(keepends=True)
        sys.stdout.writelines(difflib.unified_diff(a, b, str(path), str(path), lineterm=""))
        sys.stdout.write("\n")
        return "would-change", None
    if mode == "check":
        return "would-change", None

    out = new_body + ("\n" if had_final_nl else "")
    if newline == "\r\n":
        out = out.replace("\n", "\r\n")
    path.write_bytes(out.encode("utf-8"))
    return "changed", None


def gather(paths: list[Path]):
    """Yields .java files from files, directories (recursive), or globs."""
    for p in paths:
        if p.is_file() and p.suffix == ".java":
            if not any(part in EXCLUDED_DIR_PARTS for part in p.parts):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.java")):
                if not any(part in EXCLUDED_DIR_PARTS for part in f.parts):
                    yield f
        else:
            for f in sorted(p.parent.glob(p.name)):
                if f.suffix == ".java":
                    yield f


def run(paths: list[str], mode: str = "write") -> dict:
    """Reorders (or checks/diffs) every .java file resolved from paths.

    Args:
        paths: files, directories, or globs.
        mode: one of write, check, diff.

    Returns:
        dict with scanned, changed, would_change, skipped, errors, and details.
    """
    files = list(gather([Path(p) for p in paths]))
    counts = {"changed": 0, "would_change": 0, "skipped": 0, "errors": 0}
    details: list[dict] = []
    for path in files:
        status, detail = process_file(path, mode)
        if status == "changed":
            counts["changed"] += 1
            details.append({"path": str(path), "status": status})
        elif status == "would-change":
            counts["would_change"] += 1
            details.append({"path": str(path), "status": status})
        elif status == "skipped":
            counts["skipped"] += 1
            details.append({"path": str(path), "status": status, "reason": detail})
        elif status == "error":
            counts["errors"] += 1
            details.append({"path": str(path), "status": status, "reason": detail})
    return {"scanned": len(files), **counts, "details": details}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="toolsmith.imports",
        description="Reorder Java imports to the IntelliJ Default layout.")
    ap.add_argument("paths", nargs="+")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true",
                   help="report files that would change; exit 1 if any do")
    g.add_argument("--diff", action="store_true",
                   help="print a unified diff instead of writing")
    args = ap.parse_args(argv)
    mode = "check" if args.check else "diff" if args.diff else "write"

    result = run(args.paths, mode)
    if result["scanned"] == 0:
        print("No .java files found.", file=sys.stderr)
        return 2
    for d in result["details"]:
        if d["status"] == "changed":
            print(f"reordered      {d['path']}")
        elif d["status"] == "would-change" and mode == "check":
            print(f"would-reorder  {d['path']}")
        elif d["status"] in ("skipped", "error"):
            print(f"{d['status']:<14} {d['path']}  ({d.get('reason')})", file=sys.stderr)

    n = result["would_change"] if mode != "write" else result["changed"]
    verb = "would reorder" if mode != "write" else "reordered"
    print(f"\n{result['scanned']} scanned | {n} {verb} | "
          f"{result['skipped']} skipped | {result['errors']} errors")
    if mode == "check" and result["would_change"]:
        return 1
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
