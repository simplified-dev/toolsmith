---
name: java-find-usages
description: Find callers of a Java method, references to a class, or readers/writers of a field. Auto-invoked when the user asks "who calls X", "find usages of X", "where is X used", or before deleting / signature-changing a symbol. Routes to IntelliJ MCP `search_symbol` + `get_symbol_info` (the same engine as Find Usages in the IDE). Split out of `java-symbol-search` so usage discovery has its own entry point separate from declaration / throw-site lookups.
auto_invoke: true
tags: [java, usages, references, callers, mcp, intellij]
---

# java-find-usages

Find every reference to a Java symbol. AST-aware, overload-resolving,
inheritance-following. The right tool before deleting a method, renaming a
field, or estimating refactor blast radius.

## When to invoke

- User asks "who calls X", "find usages of X", "where is X used", "what
  references X".
- About to delete or signature-change a method, class, or field - need the
  callers first.
- Estimating refactor blast radius before committing to a rename or move.
- After `java-symbol-search` returns a declaration and the next step is
  enumerating its references.

This is the **usage discovery** entry point. `java-symbol-search` is the
**declaration discovery** entry point. Same MCP engine, different intent:

| Skill | Purpose | Typical first call |
|---|---|---|
| `java-symbol-search` | Find where `X` is *declared* / throw sites / imports | `search_symbol` to locate the decl |
| `java-find-usages` | Find where `X` is *used* (callers, references) | `get_symbol_info` on the decl to list references |

## How to use - decision tree

1. Do you already have the declaration's `(file, line, column)` of `X`?
   - Yes -> `mcp__IntelliJ_IDE__get_symbol_info` with that position. Returns
     declaration + references in one call.
   - No -> first `mcp__IntelliJ_IDE__search_symbol` with the symbol name to
     locate the declaration, then `get_symbol_info` at the result coordinates.
2. Need to disambiguate between overloads?
   - `search_symbol` returns one entry per declaration. Pick the right
     overload by signature before calling `get_symbol_info`.
3. Need text-level references (string literals, reflection, log messages
   that mention `X` by name)?
   - Fall through to `mcp__IntelliJ_IDE__search_regex` with the symbol's
     simple name as the pattern. AST search misses these.

## Cost note

`get_symbol_info` returns structured references with file + line. A single
call handles what would otherwise be a project-wide regex plus
post-processing for false positives (string matches, comments, package
fragments). The AST result also resolves inheritance: a call to
`Base::method()` from a `Sub` instance shows up correctly.

## Common probes

| Question | First call |
|---|---|
| Who calls `Foo.bar()`? | `search_symbol q="bar"` (filter to class Foo), then `get_symbol_info` at the decl |
| Where is class `Widget` referenced? | `search_symbol q="Widget"`, then `get_symbol_info` at the class decl line |
| Who reads / writes field `count`? | `search_symbol q="count"`, then `get_symbol_info` - references distinguish read vs write in some IDE versions; otherwise filter with `search_regex` |
| Find string-literal references to `"FooEvent"` | `search_regex q='"FooEvent"'` - AST tools won't find these |

## Fallback

If the IDE is not attached, `Grep` with the simple name as pattern and
`glob: "**/*.java"`. Note the degradation:

- Cannot resolve overloads - all overloads' callers show up together.
- Cannot follow inheritance - `Sub::method()` calls that resolve through
  `Base` are missed unless you grep both names.
- False positives on string literals, comments, partial-name matches.

## Cross-reference

- Declaration / throw-site / import discovery -> `java-symbol-search`.
- Renaming after finding usages -> `java-bulk-rename`.
- Post-edit verification -> `gradle-verify-gate`.

## After running

If the next step is a rename or signature change, route through
`java-bulk-rename` - it consumes the same MCP `(file, line, column)` and
performs the type-aware refactor atomically.
