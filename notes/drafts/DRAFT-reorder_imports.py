#!/usr/bin/env python3
"""Reorder Java imports to match the IntelliJ built-in Default import layout.

This is the IDE-independent fallback used when the IntelliJ MCP is not
attached. It reproduces - byte for byte - what `Optimize Imports` produces
under the Default scheme (the authoritative scheme for this codebase, which
ships no custom IMPORT_LAYOUT_TABLE and no .editorconfig).

Derived empirically from 48 committed source files across all four family
roots (see DRAFT-import-order.md for the citations). The layout is:

    <all other non-static imports, ASCII-sorted>      # group 1
    <blank line>
    <javax.* ASCII-sorted, then java.* ASCII-sorted>  # group 2
    <blank line>
    <all static imports, ASCII-sorted>                # group 3

Rules that make this faithful and NOT reproducible by a naive line sort:
  - Group 2 is NOT alphabetical: javax.* precedes java.* even though a flat
    string sort would rank "java." before "javax" ('.'=46 < 'x'=120).
  - `jakarta.*`, `io.*`, `net.*`, `reactor.*`, etc. are "all other" (group 1);
    only the exact top segments `java` and `javax` are special-cased.
  - Sorting is flat-string ASCII (Java String.compareTo == Python str compare):
    an upper-cased class segment sorts before a lower-cased sub-package at the
    same depth (e.g. `...http.URIScheme` before `...http.config.RegistryBuilder`
    because 'U'=85 < 'c'=99). Case-insensitive sorts get this wrong.
  - Static imports form ONE trailing group regardless of package.
  - Existing wildcards (`foo.*`, `import static foo.Bar.*`) are preserved
    verbatim - never expanded, never collapsed, never invented (that needs
    classpath knowledge this tool does not have).

Safety:
  - CRLF vs LF is detected and restored; trailing-newline state is preserved.
  - Only the contiguous import region (first import line .. last import line) is
    rewritten. The package statement, any leading license/header comment, and
    all code below are untouched.
  - If a non-blank, non-import line (e.g. a comment) sits BETWEEN imports, the
    file is skipped and reported rather than risk destroying the comment.
  - Idempotent: a second run produces identical bytes.

Usage:
    reorder_imports.py PATH [PATH...]     # rewrite in place, print a summary
    reorder_imports.py --check PATH...    # report what WOULD change; exit 1 if any
    reorder_imports.py --diff PATH...     # print a unified diff, do not write

PATH may be a .java file, a directory (recursive .java walk), or a glob.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

# import [static] <dotted.path[.*]> ; with optional trailing spaces.
import re

IMPORT_RE = re.compile(
    r'^import(?P<static>\s+static)?\s+(?P<path>[\w.]+(?:\.\*)?)\s*;[ \t]*$'
)

EXCLUDED_DIR_PARTS = {
    'build', '.gradle', '.idea', '.intellijPlatform', 'out', 'target',
    'node_modules', '.git', 'bin', 'classes', 'generated', 'generated-sources',
}


def _top_segment(path: str) -> str:
    """First dot-delimited segment of an import path, e.g. 'java' or 'jakarta'."""
    return path.split('.', 1)[0]


# ---------------------------------------------------------------- core reorder
def reorder_text(text_lf: str) -> tuple[str, bool, str | None]:
    """Reorders the import block of LF-normalized source.

    Returns (new_text, changed, skip_reason). skip_reason is non-None when the
    file was deliberately left untouched for a safety reason (still changed=False).
    """
    lines = text_lf.split('\n')

    # Locate the import region: first and last physical line that is an import.
    idx = [i for i, ln in enumerate(lines) if IMPORT_RE.match(ln)]
    if not idx:
        return text_lf, False, None
    first, last = idx[0], idx[-1]

    # Refuse to touch a block that interleaves non-import, non-blank lines
    # (comments / annotations); reordering would drop or misplace them.
    for i in range(first, last + 1):
        stripped = lines[i].strip()
        if stripped and not IMPORT_RE.match(lines[i]):
            return text_lf, False, 'non-import line inside import block'

    javax_b: list[str] = []
    java_b: list[str] = []
    other_b: list[str] = []
    static_b: list[str] = []
    for i in range(first, last + 1):
        m = IMPORT_RE.match(lines[i])
        if not m:
            continue
        path = m.group('path')
        if m.group('static'):
            static_b.append(f'import static {path};')
        else:
            seg = _top_segment(path)
            line = f'import {path};'
            if seg == 'javax':
                javax_b.append(line)
            elif seg == 'java':
                java_b.append(line)
            else:
                other_b.append(line)

    def _sorted_unique(block: list[str], key_start: int) -> list[str]:
        # key_start strips the leading 'import ' / 'import static ' so the sort
        # key is the dotted path only - identical ordering to Java compareTo.
        return sorted(dict.fromkeys(block), key=lambda s: s[key_start:])

    other_b = _sorted_unique(other_b, len('import '))
    javax_b = _sorted_unique(javax_b, len('import '))
    java_b = _sorted_unique(java_b, len('import '))
    static_b = _sorted_unique(static_b, len('import static '))

    groups: list[list[str]] = []
    if other_b:
        groups.append(other_b)
    if javax_b or java_b:
        groups.append(javax_b + java_b)
    if static_b:
        groups.append(static_b)

    rebuilt = '\n\n'.join('\n'.join(g) for g in groups)

    new_lines = lines[:first] + rebuilt.split('\n') + lines[last + 1:]
    new_text = '\n'.join(new_lines)
    return new_text, new_text != text_lf, None


# ---------------------------------------------------------------- file I/O
def process_file(path: Path, mode: str) -> tuple[str, str | None]:
    """Handles one file. mode in {'write','check','diff'}.

    Returns (status, detail). status in
    {'unchanged','changed','would-change','skipped','error'}.
    """
    try:
        raw = path.read_bytes()
    except (PermissionError, OSError) as exc:
        return 'error', str(exc)
    newline = '\r\n' if b'\r\n' in raw else '\n'
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return 'error', 'not utf-8'
    had_final_nl = text.endswith('\n') or text.endswith('\r\n')
    text_lf = text.replace('\r\n', '\n')
    # Drop a single trailing newline for stable line math; restore on write.
    body = text_lf[:-1] if text_lf.endswith('\n') else text_lf

    new_body, changed, skip = reorder_text(body)
    if skip:
        return 'skipped', skip
    if not changed:
        return 'unchanged', None

    if mode == 'diff':
        a = body.splitlines(keepends=True)
        b = new_body.splitlines(keepends=True)
        sys.stdout.writelines(
            difflib.unified_diff(a, b, str(path), str(path), lineterm='')
        )
        sys.stdout.write('\n')
        return 'would-change', None
    if mode == 'check':
        return 'would-change', None

    out = new_body + ('\n' if had_final_nl else '')
    if newline == '\r\n':
        out = out.replace('\n', '\r\n')
    path.write_bytes(out.encode('utf-8'))
    return 'changed', None


def gather(paths: list[Path]):
    for p in paths:
        if p.is_file() and p.suffix == '.java':
            if not any(part in EXCLUDED_DIR_PARTS for part in p.parts):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob('*.java')):
                if not any(part in EXCLUDED_DIR_PARTS for part in f.parts):
                    yield f
        else:
            for f in sorted(p.parent.glob(p.name)):
                if f.suffix == '.java':
                    yield f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog='reorder_imports',
        description='Reorder Java imports to the IntelliJ Default layout.')
    ap.add_argument('paths', nargs='+', type=Path)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--check', action='store_true',
                   help='report files that would change; exit 1 if any do')
    g.add_argument('--diff', action='store_true',
                   help='print a unified diff instead of writing')
    args = ap.parse_args(argv)
    mode = 'check' if args.check else 'diff' if args.diff else 'write'

    files = list(gather(args.paths))
    if not files:
        print('No .java files found.', file=sys.stderr)
        return 2

    changed = would = skipped = errors = 0
    for path in files:
        status, detail = process_file(path, mode)
        if status == 'changed':
            changed += 1
            print(f'reordered  {path}')
        elif status == 'would-change':
            would += 1
            if mode == 'check':
                print(f'would-reorder  {path}')
        elif status == 'skipped':
            skipped += 1
            print(f'skipped    {path}  ({detail})', file=sys.stderr)
        elif status == 'error':
            errors += 1
            print(f'error      {path}  ({detail})', file=sys.stderr)

    verb = 'would reorder' if mode != 'write' else 'reordered'
    n = would if mode != 'write' else changed
    print(f'\n{len(files)} scanned | {n} {verb} | '
          f'{skipped} skipped | {errors} errors')
    # --check is a gate: non-zero when anything is out of order.
    if mode == 'check' and would:
        return 1
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
