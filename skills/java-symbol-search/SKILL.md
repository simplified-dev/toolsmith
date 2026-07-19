---
name: java-symbol-search
description: Search Java codebases for symbols, throw sites, extends/implements relationships, imports, or call sites. Auto-invoked BEFORE Claude runs Grep for patterns like `throw new`, `extends`, `implements`, `import a.b.c`, or method/class call sites. Routes to IntelliJ MCP `search_symbol`, `search_regex`, `find_files_by_glob`, and `get_symbol_info` for AST-aware structured results; falls back to Grep only when the IDE is not attached.
auto_invoke: true
tags: [java, search, mcp, intellij, grep-alternative]
---

# java-symbol-search

Route Java symbol / throw-site / import / caller lookups to IntelliJ MCP tools
instead of `Grep`. AST-aware results in one call beat regex over `**/*.java`.

## When to invoke

- About to `Grep` for `throw new X`, `extends X`, `implements X`, or
  `import a.b.c.X`.
- User asks "find subclasses / implementors / throw sites of X".
- About to enumerate Java files by glob to feed a follow-up tool.

For **usage discovery** ("who calls X", "where is X referenced") route to
`java-find-usages` instead - same MCP engine, dedicated skill so the intent
stays explicit.

For **wildcard / unused import audits** route to `java-import-audit` - it
owns the inspection routing.

Threshold: if you would write `grep -rn ... --include="*.java"`, invoke this
skill first.

## How to use - triage table

| Intent | MCP tool | Notes |
|---|---|---|
| Find all throw sites of `X` | `mcp__IntelliJ_IDE__search_regex` | Pattern: `throw\s+new\s+X\b` |
| Find subclasses or implementors of `X` | `mcp__IntelliJ_IDE__search_symbol` then `mcp__IntelliJ_IDE__get_symbol_info` | `get_symbol_info` lists inheritors |
| Find callers of `method()` / references to class | -> `java-find-usages` | Dedicated skill for usage discovery |
| Find files importing FQN `a.b.c.X` | `mcp__IntelliJ_IDE__search_regex` | Pattern: `^import\s+a\.b\.c\.X;` |
| Find wildcard imports | -> `java-import-audit` | Dedicated skill for import hygiene |
| Enumerate files by glob | `mcp__IntelliJ_IDE__find_files_by_glob` | E.g. `**/*Exception.java` |
| Inspect a class / method shape | `mcp__IntelliJ_IDE__get_symbol_info` | Returns declared members + references |

## Cost note

A single `mcp__IntelliJ_IDE__search_regex` call returns structured `{file, line, snippet}` hits with the right scoping. The equivalent `Grep` needs `-n -C 2 --glob="**/*.java"` plus post-processing of the output. AST-backed
tools also resolve overloads and inheritance correctly; regex does not.

## Fallback

If no IDE process is attached (the `mcp__IntelliJ_IDE__*` tools error out),
fall back to `Grep` with:

- `glob: "**/*.java"`
- `-n` (line numbers)
- `-C 2` (context)
- `output_mode: "content"` when you need the surrounding lines

Note the degradation in your reply so the user knows the result is text-only
and may miss reflective / generated references.

## After running

If your search is the prelude to a bulk edit or rename, route the next step
through `java-bulk-rename`. After any multi-file change, invoke
`gradle-verify-gate` before committing.
