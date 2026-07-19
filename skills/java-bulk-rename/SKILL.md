---
name: java-bulk-rename
description: Rename Java packages, classes, methods, or fields across a codebase. Auto-invoked BEFORE Claude runs `find ... -exec sed -i` against `.java` files or fires batched Edit calls across more than one Java file. Routes to IntelliJ `mcp__IntelliJ_IDE__rename_refactoring` for type-aware renames that update imports, usages, and package directory layout atomically; falls back to `sed` only for genuine text-only swaps such as comment or log-message tokens.
auto_invoke: true
tags: [java, refactor, rename, mcp, intellij, sed-alternative]
---

# java-bulk-rename

Route Java renames through IntelliJ's type-aware refactor instead of `sed` or
Edit loops. A type-aware rename updates declarations, usages, imports, and
(for packages) directory moves in a single atomic call.

## When to invoke

- About to `find src -name "*.java" -exec sed -i ...` for a class / method /
  field / package rename.
- About to issue more than one `Edit` against `.java` files to rename the same
  symbol.
- User asks "rename package X to Y", "rename class X", or "rename method X".
- A previous `java-symbol-search` pass has surfaced N>3 hits all of which need
  the same identifier swap.

## How to use - decision tree

1. Is this a type-aware rename (class, interface, enum, method, field, or
   package)? Use `mcp__IntelliJ_IDE__rename_refactoring`.
2. Is this a text-only swap (string literal, comment token, log message, file
   name unrelated to a Java symbol)? Use `Bash` with `sed -i`.
3. More than 3 files would be touched? Always prefer
   `mcp__IntelliJ_IDE__rename_refactoring` over an Edit loop, even when the
   change is technically text-only - it is faster and avoids the read-before-
   edit gate noted below.

## Why MCP over sed

- `rename_refactoring` updates the declaration, every usage, and every import
  in one pass; `sed` leaves stale `import` lines until a separate grep sweep.
- Package rename also moves the directory and rewrites the `package`
  statement; `sed` cannot move files.
- Overloaded methods are disambiguated by signature; `sed` cannot tell two
  overloads apart.
- Refactor preview shows conflicts (collisions, hidden refs) before applying.

## Interaction with the Read-before-Edit rule

User `CLAUDE.md` `## File Editing` requires a prior `Read` at the file's
current path before any `Edit`. `mcp__IntelliJ_IDE__rename_refactoring`
short-circuits this rule because it operates on the symbol through the IDE,
not through the harness `Edit` tool. After a rename, re-`Read` any file you
plan to modify with `Edit` afterward; the rename invalidated your prior reads
of any file it touched.

## Fallback

If the IDE is not attached, use `Bash` `sed -i` for text-only swaps. For
type-aware renames without the IDE, prefer a scoped Edit loop driven by
`java-symbol-search` output rather than blind `sed`; flag the degradation in
your reply.

## After running

1. Reformat every file touched by the rename. Call
   `mcp__IntelliJ_IDE__reformat_file` on each affected path - this uses the
   project's live IntelliJ code-style settings (the only formatter that does).
   Skip `google-java-format` / Spotless / IntelliJ headless: they impose their
   own style and will fight project-specific rules.
   - `rename_refactoring` already preserves formatting in most cases, so this
     step is cheap; run it anyway for fall-back `sed` paths where
     indentation drift is possible.
2. Invoke `gradle-verify-gate` to confirm the project still compiles.
   Cross-module renames warrant the root build; single-module renames are
   fine with module-scoped verification.
