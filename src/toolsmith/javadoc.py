#!/usr/bin/env python3
"""Audit and normalize Java javadocs against user-CLAUDE.md conventions.

Usage:
    normalize.py [--fix] [--scope class|method|field|all] PATH [PATH...]

Without --fix: prints a report.
With    --fix: applies safe auto-fixes in place, then prints a report.

PATH may be a .java file, a directory (recursive .java walk), or a glob.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------- safe fixes
# Each: (id, pattern, replacement, scope_tag). scope_tag in {"any","line"}.

SAFE_FIXES: list[tuple[str, re.Pattern[str], str]] = [
    # Promote single-line class/method/field /** xxx */ to block form,
    # except {@inheritDoc} overrides which are intentional one-liners.
    ("oneliner-to-block",
     re.compile(r'^([ \t]*)/\*\* (?!\{@inheritDoc\})(.+?) \*/$', re.MULTILINE),
     r'\1/**\n\1 * \2\n\1 */'),

    # ` -- ` -> ` - ` inside javadoc lines (anchored to ` * ` prefix).
    ("dashes",
     re.compile(r'(^\s*\*.*?)\s--\s', re.MULTILINE),
     r'\1 - '),

    # em-dash / en-dash -> -, inside javadoc lines.
    ("emdash",
     re.compile(r'(^\s*\*.*?)\s[—–]\s', re.MULTILINE),
     r'\1 - '),

    # &mdash; entity -> -, inside javadoc lines.
    ("mdash-entity",
     re.compile(r'(^\s*\*.*?)\s&mdash;\s', re.MULTILINE),
     r'\1 - '),

    # @author and @since lines -> delete the whole line.
    ("author-tag",
     re.compile(r'^[ \t]*\*[ \t]*@author[^\n]*\n', re.MULTILINE),
     ''),
    ("since-tag",
     re.compile(r'^[ \t]*\*[ \t]*@since[^\n]*\n', re.MULTILINE),
     ''),

    # Strip trailing period on single-line @param / @return / @throws fragments.
    ("tag-trailing-period",
     re.compile(r'(^[ \t]*\*[ \t]*@(?:param[ \t]+<?\w+>?|return|throws[ \t]+\w+)[ \t]+[^\n]*?[^.\s])\.([ \t]*\n)',
                re.MULTILINE),
     r'\1\2'),

    # Column-aligned `@param name      desc` -> single space.
    ("param-column-align",
     re.compile(r'(@param[ \t]+<?\w+>?)[ \t]{2,}(\S)'),
     r'\1 \2'),
]

# ---------------------------------------------------------------- FQN auto-import
# When --fix is on, FQN {@link}/{@linkplain} refs get rewritten to use the
# simple name and the corresponding import is added. Skipped (and reported as
# flags) when the simple name conflicts with a local declaration or an existing
# import of a different FQN.
#
# The prefix list is conservative on purpose: only FQNs whose top-level
# segment lives in DEFAULT_PREFIXES are simplified. Other top-level segments
# (e.g. `a.b.c.X`) are left alone to avoid false positives in projects that
# use single-letter top-level packages for non-Java reasons. Extend at the
# CLI with `--prefix foo` (repeatable, additive) for project-specific roots
# without editing this file.

DEFAULT_PREFIXES: list[str] = [
    'java', 'javax', 'com', 'org', 'net', 'dev', 'io', 'lib',
]


def _build_fqn_patterns(prefixes: list[str]) -> tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """Builds (link, param, flag) regexes from the active prefix list.

    All three are anchored on the same `(<prefix>|<prefix>|...)` alternation
    so adding a single prefix keeps the auto-import, param-FQN sub-pass, and
    the audit-only `fqn-link` flag in lockstep.
    """
    alt = '|'.join(re.escape(p) for p in prefixes)
    link_re = re.compile(
        r'\{@(link|linkplain)\s+'
        r'((?:' + alt + r')(?:\.\w+)+)'        # FQN class path
        r'(#\w+(?:\([^)]*\))?)?'               # optional #member
        r'((?:\s+[^}]+)?)'                     # optional label text
        r'\}'
    )
    # Standalone FQN inside a member signature's param list, e.g.
    # {@link X#m(java.util.function.Function)} - the inner FQN.
    param_re = re.compile(r'(?:' + alt + r')(?:\.\w+)+\.[A-Z]\w*')
    # Audit-only flag pattern - same prefix set, looser tail. Requires at
    # least one upper-cased segment somewhere in the path so package-only
    # links like {@link foo.bar.subpkg subpkg} (which can't be imported in
    # Java - only types are importable) don't fire false positives.
    flag_re = re.compile(
        r'\{@(?:link|linkplain)\s+((?:' + alt + r')(?:\.\w+)*?\.[A-Z][\w$]*'
        r'(?:\.[A-Z][\w$]*)*'                              # optional inner classes
        r'(?:#[\w$]+(?:\([^)]*\))?)?)[\w\s]*?\}'
    )
    return link_re, param_re, flag_re


# Module-load initialization from the default prefix set. main() can later
# re-install a wider set via `--prefix` before any process() call fires.
FQN_LINK_RE, PARAM_FQN_RE, _FQN_LINK_FLAG_RE = _build_fqn_patterns(DEFAULT_PREFIXES)


def _install_prefixes(prefixes: list[str]) -> None:
    """Rebuilds module-level FQN regexes from the given prefix list.

    Updates the auto-import pair (FQN_LINK_RE, PARAM_FQN_RE) and the
    fqn-link FLAGS entry in place so process() picks them up immediately.
    Safe to call before or after FLAGS is defined - the FLAGS rewrite is
    no-op when the list is missing or empty.
    """
    global FQN_LINK_RE, PARAM_FQN_RE
    link_re, param_re, flag_re = _build_fqn_patterns(prefixes)
    FQN_LINK_RE = link_re
    PARAM_FQN_RE = param_re
    for i, entry in enumerate(FLAGS):
        if entry[0] == 'fqn-link':
            FLAGS[i] = ('fqn-link', flag_re, entry[2], entry[3])
            break

IMPORT_RE = re.compile(
    r'^import(?P<static>\s+static)?\s+(?P<path>[\w.]+(?:\.\*)?);[ \t]*\n',
    re.MULTILINE,
)


def _is_wildcard(path: str) -> bool:
    return path.endswith('.*')


def _import_line(static: bool, path: str) -> str:
    """Emits an `import [static] PATH;` line for a parsed import."""
    return f"import{' static' if static else ''} {path};"


def _scan_imports(text: str) -> tuple[dict[str, str], set[str]]:
    """Scans the file for imports relevant to FQN-conflict resolution.

    Returns:
      - simple_to_fqn: {simple_name: fqn} for non-static, non-wildcard imports.
        Static imports are excluded because their simple name is a member,
        not a type, so they can't shadow a `{@link Type}` ref.
      - wildcard_pkgs: package prefixes from `import pkg.*;` (no trailing
        `.*`). A wildcard satisfies any simple name from that package, so
        the auto-import phase must NOT add an explicit import for a name
        already covered by one.
    """
    simple_to_fqn: dict[str, str] = {}
    wildcard_pkgs: set[str] = set()
    for m in IMPORT_RE.finditer(text):
        path = m.group('path')
        if _is_wildcard(path):
            wildcard_pkgs.add(path[:-2])  # drop the trailing ".*"
        elif not m.group('static'):
            simple_to_fqn[path.split('.')[-1]] = path
    return simple_to_fqn, wildcard_pkgs

TYPE_DECL_RE = re.compile(
    r'^(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed)\s+)*'
    r'(?:class|interface|record|enum)\s+(\w+)',
    re.MULTILINE
)

PACKAGE_RE = re.compile(r'^package\s+[\w.]+;[ \t]*\n', re.MULTILINE)


def _simplify_fqn(fqn: str) -> tuple[str, str]:
    """Splits FQN into (package, class_path). class_path keeps dots for inner classes."""
    segs = fqn.split('.')
    for i, s in enumerate(segs):
        if s and s[0].isupper():
            return '.'.join(segs[:i]), '.'.join(segs[i:])
    return fqn, ''


def fix_fqn_links(text: str) -> tuple[str, int, list[tuple[int, str]]]:
    """Auto-imports + simplifies FQN javadoc refs. Returns (new_text, fix_count, skips)."""
    imports, wildcard_pkgs = _scan_imports(text)
    decls: set[str] = {m.group(1) for m in TYPE_DECL_RE.finditer(text)}
    pending: dict[str, str] = {}
    edits: list[tuple[int, int, str]] = []
    skips: list[tuple[int, str]] = []

    def _line(pos: int) -> int:
        return text.count('\n', 0, pos) + 1

    def _covered_by_wildcard(fqn: str) -> bool:
        """`import pkg.*;` makes `pkg.X` already in scope - simplify without adding."""
        pkg = fqn.rsplit('.', 1)[0]
        return pkg in wildcard_pkgs

    for m in FQN_LINK_RE.finditer(text):
        tag, fqn, member, label = m.group(1), m.group(2), m.group(3) or '', m.group(4) or ''
        _pkg, class_path = _simplify_fqn(fqn)
        if not class_path:
            continue
        simple = class_path.split('.')[-1]

        # Conflicts
        seen = {**imports, **pending}
        if simple in decls:
            skips.append((_line(m.start()), f"{fqn}{member} - '{simple}' declared in file"))
            continue
        if simple in seen and seen[simple] != fqn:
            skips.append((_line(m.start()), f"{fqn}{member} - '{simple}' already imports {seen[simple]}"))
            continue

        # Schedule import for the outer class - unless covered by an existing
        # `import pkg.*;` wildcard (in which case the simple name is already in
        # scope and we must NOT also add an explicit import that would clash
        # with the wildcard or duplicate the resolution).
        if (simple not in seen
                and not fqn.startswith('java.lang.')
                and not _covered_by_wildcard(fqn)):
            pending[simple] = fqn

        # Sub-pass: simplify FQN param types inside the member signature.
        # Each match either gets simplified+scheduled or left as FQN on conflict.
        outer_line = _line(m.start())

        def _simplify_param(mm: re.Match[str]) -> str:
            param_fqn = mm.group(0)
            _, cls = _simplify_fqn(param_fqn)
            if not cls:
                return param_fqn
            ps = cls.split('.')[-1]
            cur = {**imports, **pending}
            if ps in decls:
                skips.append((outer_line, f"{param_fqn} - '{ps}' declared in file"))
                return param_fqn
            if ps in cur and cur[ps] != param_fqn:
                skips.append((outer_line, f"{param_fqn} - '{ps}' already imports {cur[ps]}"))
                return param_fqn
            if (ps not in cur
                    and not param_fqn.startswith('java.lang.')
                    and not _covered_by_wildcard(param_fqn)):
                pending[ps] = param_fqn
            return ps

        new_member = PARAM_FQN_RE.sub(_simplify_param, member) if '(' in member else member
        edits.append((m.start(), m.end(), f'{{@{tag} {simple}{new_member}{label}}}'))

    if not edits:
        return text, 0, skips

    new_text = text
    for start, end, rep in sorted(edits, reverse=True):
        new_text = new_text[:start] + rep + new_text[end:]

    if pending:
        new_text = _inject_imports(new_text, pending.values())
    return new_text, len(edits), skips


def _inject_imports(text: str, new_fqns) -> str:
    """Inserts new imports, preserving existing group structure (blank-line
    separated blocks).

    Existing imports are preserved EXACTLY as written: `import static foo.Bar.x`
    stays static, `import foo.*` stays a wildcard. Only the new FQNs from
    `new_fqns` are added (always as plain, non-static, non-wildcard imports
    since they come from the FQN-auto-import phase which only handles type
    references).

    New imports route to whichever existing group contains an import with the
    same top-level package prefix; otherwise they go to the java-group
    (for java.*/javax.*) or the first non-java group.
    """
    matches = list(IMPORT_RE.finditer(text))
    # Compare against full `import [static] PATH;` text so a new
    # `java.util.List` doesn't accidentally collapse with an existing
    # `import static java.util.List.of` or `import java.util.*`.
    existing_lines = {_import_line(bool(m.group('static')), m.group('path'))
                      for m in matches}
    to_add_lines = {_import_line(False, fqn) for fqn in new_fqns} - existing_lines
    if not to_add_lines:
        return text

    if not matches:
        # No existing imports: emit two groups (non-java, then java) when both
        # are present, separated by a blank line.
        pkg = PACKAGE_RE.search(text)
        insert = pkg.end() if pkg else 0
        java = sorted(i for i in to_add_lines
                      if i.startswith(('import java.', 'import javax.')))
        other = sorted(to_add_lines - set(java))
        chunks = []
        if other:
            chunks.append('\n'.join(other))
        if java:
            chunks.append('\n'.join(java))
        block = '\n' + '\n\n'.join(chunks) + '\n'
        return text[:insert] + block + text[insert:]

    # Parse existing groups by walking the block between the first and last
    # import match, splitting on blank lines. Preserve each import line
    # verbatim so static / wildcard forms survive the rebuild.
    block_start = matches[0].start()
    block_end = matches[-1].end()
    block_text = text[block_start:block_end]

    groups: list[list[str]] = []
    current: list[str] = []
    for line in block_text.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                groups.append(current)
                current = []
        elif stripped.startswith('import '):
            current.append(stripped)
    if current:
        groups.append(current)

    def _top_prefix(line: str) -> str:
        """Top-level package segment of an import line, e.g. 'java' or 'dev'."""
        body = line[len('import '):]
        if body.startswith('static '):
            body = body[len('static '):]
        return body.split('.', 1)[0]

    # Locate the "java group" - the group whose imports are all java.*/javax.*.
    java_idx = -1
    for i, g in enumerate(groups):
        if g and all(_top_prefix(line) in ('java', 'javax') for line in g):
            java_idx = i
            break

    def _route(line: str) -> None:
        # 1. Prefer a group that already contains an import sharing the same
        #    top-level package segment (e.g. dev.x -> the dev.* group).
        prefix = _top_prefix(line)
        for i, g in enumerate(groups):
            if any(_top_prefix(e) == prefix for e in g):
                g.append(line)
                return
        # 2. java/javax goes to the java group, creating one if needed.
        is_java = prefix in ('java', 'javax')
        if is_java:
            nonlocal java_idx
            if java_idx >= 0:
                groups[java_idx].append(line)
            else:
                groups.append([line])
                java_idx = len(groups) - 1
            return
        # 3. Otherwise drop into the first non-java group, or create one.
        for i, g in enumerate(groups):
            if i != java_idx:
                g.append(line)
                return
        groups.insert(0, [line])
        if java_idx >= 0:
            java_idx += 1

    for line in sorted(to_add_lines):
        _route(line)

    for g in groups:
        g.sort()

    rebuilt = '\n\n'.join('\n'.join(g) for g in groups) + '\n'
    return text[:block_start] + rebuilt + text[block_end:]


# ---------------------------------------------------------------- flag rules
# Each: (id, pattern, message, required_scope). required_scope=None means fire
# in any context; otherwise the flag only fires when context_at() matches.

FLAGS: list[tuple[str, re.Pattern[str], str, str | None]] = [
    # FQN refs that the auto-fix couldn't handle (or weren't run through --fix
    # yet). After --fix, only conflict-skipped FQNs remain visible here. The
    # pattern is sourced from _build_fqn_patterns so a `--prefix` CLI extension
    # keeps audit and fix in lockstep.
    ("fqn-link",
     _FQN_LINK_FLAG_RE,
     "FQN javadoc ref - import the type and use simple name",
     None),

    # Field-like javadoc that starts with `Gets ` or `Returns ` - rule violation
    # (the field/record component javadoc should be a noun-phrase fragment).
    # METHOD-context "Returns X" is fine (3rd-person verb), so this only fires
    # when the next decl below the javadoc is a field/component.
    ("gets-prefix",
     re.compile(r'/\*\*\s*\n\s*\*\s+(Gets|Returns)\s+', re.MULTILINE),
     "field/record-component javadoc starts with 'Gets'/'Returns' - use a noun-phrase fragment, drop @return",
     'field'),

    # @author / @since (in case --fix wasn't run yet, still flag).
    ("author-tag-flag",
     re.compile(r'@author\b'),
     "@author is disallowed by project style",
     None),
    ("since-tag-flag",
     re.compile(r'@since\b'),
     "@since is disallowed by project style",
     None),
]

# ---------------------------------------------------------------- scope filter
# When --scope != all, restrict each rule to javadocs whose target context
# matches. We use a simple post-pass: each match's location is examined for
# context, and rejected if it doesn't fit.

CLASS_RE  = re.compile(r'\b(?:class|interface|record|enum)\s+\w+')
METHOD_RE = re.compile(r'\)\s*(?:throws[\s\w,]+)?\s*[;{]')
FIELD_RE  = re.compile(r'(?:^|[\s;}])(?:private|protected|public|static|final|@\w+(?:\([^)]*\))?\s+)+[\w<>?,\s]+\s+\w+\s*[=;]', re.MULTILINE)


def context_at(src: str, pos: int) -> str:
    """Returns class | method | field | other, based on the next decl line below the match."""
    tail = src[pos:pos + 400]
    # Skip past the closing */ then to the next non-blank line.
    end = tail.find('*/')
    if end >= 0:
        tail = tail[end + 2:]
    # Skip lines that are annotations / lombok markers.
    for line in tail.splitlines():
        s = line.strip()
        if not s or s.startswith('@'):
            continue
        if CLASS_RE.search(s):
            return 'class'
        if METHOD_RE.search(s) or '(' in s and s.endswith('{'):
            return 'method'
        if FIELD_RE.search(s) or (';' in s and '(' not in s):
            return 'field'
        return 'other'
    return 'other'


# ---------------------------------------------------------------- core
@dataclass
class Result:
    path: Path
    fixes: list[tuple[str, int]]
    flags: list[tuple[int, str, str]]

    @property
    def empty(self) -> bool:
        return not self.fixes and not self.flags


EXCLUDED_DIR_PARTS = {
    'build', '.gradle', '.idea', '.intellijPlatform', 'out', 'target',
    'node_modules', '.git', 'bin', 'classes', 'generated', 'generated-sources',
}


def process(path: Path, fix: bool, scope: str) -> Result:
    try:
        raw = path.read_bytes()
    except (PermissionError, OSError):
        return Result(path, [], [])
    # Detect line-ending style so writes don't silently convert CRLF -> LF
    # on Windows-checked-out files. Default to platform native if no newline
    # is present (file with single trailing line or empty).
    if b'\r\n' in raw:
        newline = '\r\n'
    elif b'\n' in raw:
        newline = '\n'
    else:
        newline = '\n'
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        return Result(path, [], [])
    # Normalize to LF internally so regex patterns anchor predictably; restore
    # the original newline on write below.
    text = text.replace('\r\n', '\n')
    orig = text
    # package-info.java is an exception to the import-the-link-target rule: it
    # uses inline FQN {@link}/{@linkplain}/@see refs with NO imports (IntelliJ
    # forces FQN state in package docs and fights any imports there). So both
    # the FQN auto-import fix and the fqn-link flag are suppressed for it; the
    # other rules (dashes, em-dash, @author/@since, ...) still apply.
    is_pkg_info = path.name == 'package-info.java'
    fixes: list[tuple[str, int]] = []
    fqn_skips: list[str] = []

    if fix:
        for rid, pat, repl in SAFE_FIXES:
            new, n = pat.subn(repl, text)
            if n > 0:
                fixes.append((rid, n))
                text = new
        if not is_pkg_info:
            text, fqn_n, fqn_skips = fix_fqn_links(text)
            if fqn_n > 0:
                fixes.append(('fqn-auto-import', fqn_n))
        if text != orig:
            out = text if newline == '\n' else text.replace('\n', '\r\n')
            path.write_bytes(out.encode('utf-8'))

    flags: list[tuple[int, str, str]] = []
    # Surface FQN auto-import skips as flags (conflicts that couldn't be resolved).
    for line, reason in fqn_skips:
        flags.append((line, 'fqn-skip', reason))
    for fid, pat, msg, required in FLAGS:
        # In --fix mode the auto-import pass already handled what it could; the
        # generic fqn-link flag would just re-report the same conflict-skipped
        # refs that fqn-skip already covers, so suppress it.
        if fix and fid == 'fqn-link':
            continue
        # package-info.java intentionally uses inline FQN refs (see is_pkg_info).
        if is_pkg_info and fid == 'fqn-link':
            continue
        for m in pat.finditer(text):
            ctx = context_at(text, m.start())
            # Per-flag required scope: skip when the javadoc's context does not
            # match (e.g. gets-prefix only fires on field-attached javadocs).
            if required is not None and ctx != required:
                continue
            # Global --scope filter from CLI.
            if scope != 'all' and ctx != scope:
                continue
            line = text.count('\n', 0, m.start()) + 1
            snippet = m.group(0).replace('\n', ' \\n ').strip()[:90]
            flags.append((line, fid, f'{snippet}    [{msg}]'))

    return Result(path, fixes, flags)


def gather(paths: list[Path], excludes: list[str] | None = None):
    excludes = excludes or []
    def is_excluded(f: Path) -> bool:
        if any(part in EXCLUDED_DIR_PARTS for part in f.parts):
            return True
        as_posix = f.as_posix()
        return any(ex in as_posix or f.match(ex) for ex in excludes)
    for p in paths:
        if p.is_file() and p.suffix == '.java':
            if not is_excluded(p):
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob('*.java')):
                if not is_excluded(f):
                    yield f
        else:
            for f in sorted(p.parent.glob(p.name)):
                if not is_excluded(f):
                    yield f


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog='javadoc-normalize',
        description='Audit/normalize Java javadocs against project conventions.')
    ap.add_argument('paths', nargs='+', type=Path)
    ap.add_argument('--fix', action='store_true', help='Apply safe fixes in place')
    ap.add_argument('--scope', choices=['class', 'method', 'field', 'all'],
        default='all', help='Restrict flag checks to a declaration scope')
    ap.add_argument('--exclude', action='append', default=[],
        help='Path substring or glob to exclude (repeatable)')
    ap.add_argument('--prefix', action='append', default=[],
        help='Additional FQN top-level prefix to auto-import / flag '
             '(repeatable, additive to the defaults: '
             + ', '.join(DEFAULT_PREFIXES) + ')')
    args = ap.parse_args(argv)

    if args.prefix:
        _install_prefixes(DEFAULT_PREFIXES + list(args.prefix))

    files = list(gather(args.paths, args.exclude))
    if not files:
        print('No .java files found.', file=sys.stderr)
        return 1

    total_fixes = 0
    total_flags = 0
    touched = 0

    for path in files:
        r = process(path, args.fix, args.scope)
        if r.empty:
            continue
        touched += 1
        print(f'\n{r.path}')
        for rid, n in r.fixes:
            print(f'  fix  {rid:<22} x{n}')
            total_fixes += n
        for line, fid, snippet in r.flags:
            print(f'  flag L{line:<5} {fid:<18} {snippet}')
            total_flags += 1

    verb = 'applied' if args.fix else 'detectable'
    print(f'\n{len(files)} files scanned | {touched} with findings | '
          f'{total_fixes} fixes {verb} | {total_flags} flags for manual review')
    return 0


if __name__ == '__main__':
    sys.exit(main())