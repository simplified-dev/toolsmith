---
name: java-docs-normalize
description: Audit and normalize the javadocs of Java source files against user CLAUDE.md conventions. Auto-fixes block form, --/em-dashes, @author/@since, trailing periods, column-aligned @params. Flags FQN refs, Gets/Returns prefixes on field-like docs, and missing @param/@return for manual review. Claude invokes this BEFORE any manual javadoc edit across one or more Java files.
auto_invoke: true
tags: [java, javadoc, style, lint, batch-fix]
---

# java-docs-normalize

Run this skill BEFORE manually editing Java javadocs. The script handles the
mechanical bulk; you handle only what it flags.

## When to invoke

- User asks to "fix javadocs", "audit javadocs", "normalize javadoc style",
  "make javadocs consistent", or similar phrasing.
- About to make Java javadoc changes across more than one file.
- See one-line `/** ... */` headers (other than `{@inheritDoc}`), em-dashes
  (`—`, `–`), `--`, `&mdash;`, `@author`/`@since`, FQN `{@link java.x.Y}`,
  or `Gets `/`Returns ` field-like docs.

If only one file needs a single targeted edit, calling the script is overkill -
just edit. Threshold: more than one javadoc deviation or more than one file.

## How to use

Run it through the toolsmith CLI (`toolsmith java docs`), which is the same
logic the `java_docs_normalize` MCP tool exposes.

**Audit only (no changes):**

```bash
toolsmith java docs PATH [PATH ...]
```

**Apply safe auto-fixes:**

```bash
toolsmith java docs --fix PATH [PATH ...]
```

`PATH` may be a single `.java` file, a directory (recursive `.java` walk), or a
shell glob.

Optional scope filters narrow what gets touched:

- `--scope class` - class/interface/record declarations only
- `--scope method` - method declarations only
- `--scope field` - field declarations only
- `--scope all` - everything (default)

Optional FQN prefix extension (repeatable, additive to the defaults):

- `--prefix foo` - also auto-import / flag FQNs whose top-level segment is `foo`

Defaults are `java | javax | com | org | net | dev | io | lib`. Use
`--prefix` when a project uses a top-level package outside that set
(e.g. `--prefix api` for `api.acme.X`).

## Auto-fixed (safe transforms)

| Rule | Pattern | Fix |
|---|---|---|
| `oneliner-to-block` | `/** xxx */` (single line, not `{@inheritDoc}`) | promote to block form |
| `dashes` | ` -- ` inside javadoc | ` - ` |
| `emdash` | `—` or `–` | ` - ` |
| `mdash-entity` | `&mdash;` | ` - ` |
| `author-tag` | `@author ...` line | delete |
| `since-tag` | `@since ...` line | delete |
| `tag-trailing-period` | `@param x foo.` / `@return foo.` | strip period |
| `param-column-align` | `@param  name      desc` | single space |
| `fqn-auto-import` | `{@link java.x.Y.Z}` / `{@linkplain ...}` / param FQNs inside `(...)` | import the type, replace with simple name. Handles plain classes, methods (`#name()`), method refs with arg lists (incl. inner FQNs in param types), static fields (`#FOO`), inner classes (`Outer.Inner`), and labels (`{@link X#m text}`). Skips `java.lang.*` (no import needed) and `package-info.java` entirely (see exception below). |

## Flagged (manual review)

| Flag | When it fires |
|---|---|
| `fqn-skip` | Auto-import couldn't simplify because the simple name conflicts with a local type declaration **or** with an existing import of a different FQN. The flag reports `<fqn>` and the conflicting target. |
| `fqn-link` | (Audit-only mode.) Any `{@link package.x.Y}` ref - hint to run `--fix` or simplify by hand. Suppressed under `--fix` since `fqn-skip` covers the residual conflicts. Never fires on `package-info.java` (see exception below). |
| `gets-prefix` | Field/record-component javadoc starts with `Gets `/`Returns ` - rule says noun-phrase fragment, drop `@return`. Only fires when the next decl is a field; methods that legitimately say `Returns X` don't trip this. |
| `author-tag-flag` / `since-tag-flag` | Disallowed tags (only fires if `--fix` wasn't run). |

## FQN auto-import details

Only FQNs starting with `java | javax | com | org | net | dev | io | lib`
trigger the auto-import. Obscure prefixes (e.g. `a.b.c.X`) are left alone
deliberately to avoid false positives. Use the `--prefix` CLI flag
(repeatable, additive) to extend the set without editing the script;
`DEFAULT_PREFIXES` in `toolsmith/javadoc.py` is the source of truth for
what's always on.

Package-only refs - `{@link foo.bar.subpkg subpkg}` where the FQN names a
package, not a type - are correctly left alone: Java has no syntax for
importing a package, only its types. The flag pattern requires at least one
upper-cased segment so package-only links don't fire false positives.

Inner classes import as the full dotted path: `java.util.Map.Entry` becomes
`import java.util.Map.Entry;` plus `{@link Entry}`. If you prefer the
`Map.Entry` form keep the outer-class import yourself - the script defaults to
the last segment for brevity.

### `package-info.java` exception

`package-info.java` is exempt from `fqn-auto-import` and the `fqn-link` flag:
package docs intentionally use **inline FQN** `{@link}` / `{@linkplain}` / `@see`
refs and carry **no imports** (`{@link pkg.Foo Foo}`, simple-name label). IntelliJ
forces FQN state in package docs and fights any imports there, and since the file
holds no code its imports only ever backed javadoc - so the convention is inverted.
The other rules (dashes, em-dash, `@author`/`@since`, oneliner-to-block, ...) still
apply. Mirrors the `package-info.java` exception in the user-CLAUDE.md `## Javadoc`
section.

## After running

If `--fix` modified a file you previously `Read`, re-read before editing - the
in-place transforms invalidate your harness state for that file.

Run `gradle compileJava` (or the project equivalent) to confirm imports are
still consistent. Auto-fixes are pure text but javac will catch any javadoc
that referenced a now-imported simple name that the import isn't actually
providing.

## Verification & invariants

- The script never rewrites `{@inheritDoc}` overrides.
- The script never touches code outside `/** ... */` blocks (with the dash /
  em-dash exception, scoped to characters that appear in javadoc lines via the
  ` * ` prefix anchor).
- `import static foo.Bar.baz;` lines are preserved verbatim - the rebuild
  re-emits each existing import line as-is, including the `static` keyword.
- `import pkg.*;` wildcard imports are preserved verbatim AND satisfy
  auto-import resolution: `{@link pkg.X}` is simplified to `{@link X}` without
  adding a new `import pkg.X;` (which would clash with the wildcard).
- Auto-import never re-emits a line that would duplicate or downgrade an
  existing import; new lines are always plain `import FQN;` since the
  auto-import phase only handles type references, never members.

## Where this skill encodes the rules

The rules trace directly to the user-CLAUDE.md `## Javadoc` section. Any change
to that section should be mirrored here.