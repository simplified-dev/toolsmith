---
name: java-import-audit
description: Audit Java imports for wildcard usage, unused imports, and FQN javadoc refs that should be imported. Auto-invoked when the user asks to "clean up imports", "audit imports", "find wildcard imports", or before a commit that touched many `.java` files. Routes to IntelliJ MCP `get_file_problems` for unused-import detection (uses the IDE's own inspection) and `search_regex` for wildcard pattern discovery. Defers FQN-in-javadoc handling to `javadoc-normalize` (which already auto-imports).
auto_invoke: true
tags: [java, imports, audit, cleanup, mcp, intellij]
---

# java-import-audit

Audit `.java` imports for the three drift modes that don't show up in
compilation: wildcards, unused imports, and inline FQNs in javadoc. Each
mode routes to the cheapest correct tool.

## When to invoke

- User asks to "clean up imports", "audit imports", "find wildcard imports",
  "find unused imports".
- About to commit a multi-file Java change and want a hygiene check.
- A `java-symbol-search` hit on a class shows surprising callers - often
  these are dead imports left behind after a refactor.

Skip when: only one file is in scope and you can just open it in the IDE and
hit Ctrl+Alt+O. The skill is for batch / pre-commit / cross-module checks.

## How to use - triage table

| Intent | Tool | Notes |
|---|---|---|
| Find wildcard imports project-wide | `mcp__IntelliJ_IDE__search_regex` | Pattern: `^import\s+(?:static\s+)?[\w.]+\.\*;` |
| Find unused imports in one file | `mcp__IntelliJ_IDE__get_file_problems` | Returns IntelliJ's `UnusedImport` inspection results without running a full project scan |
| Find unused imports across module | `mcp__IntelliJ_IDE__build_project` then filter for unused-import warnings | Or run `./gradlew :module:compileJava` with `-Werror` and scan the output |
| Find inline FQN javadoc refs | `javadoc-normalize` with `--fix` | Handles `{@link a.b.c.X}` -> `import a.b.c.X; {@link X}` atomically; flags conflicts as `fqn-skip` |
| Find files importing FQN `a.b.c.X` | `mcp__IntelliJ_IDE__search_regex` | Pattern: `^import\s+a\.b\.c\.X;` (covered by `java-symbol-search` too) |

## Wildcard imports

The CLAUDE.md `## Javadoc` rules indirectly prohibit FQN refs in javadoc but
do not explicitly forbid wildcard imports in code. Treat wildcards as a
style warning, not an error, unless the project's `.editorconfig` /
`checkstyle` / IntelliJ inspection profile says otherwise. Surface count
and locations; let the user decide on a cleanup pass.

The IntelliJ default "Class count to use import with '\*'" is 5 - reducing
that to a higher number suppresses most legitimate wildcard usage. If a
project's IntelliJ config explicitly allows wildcards, do not flag them.

## Unused imports

`mcp__IntelliJ_IDE__get_file_problems` is the right tool because it uses
IntelliJ's own `UnusedImport` inspection. Output includes line numbers and
the import statement that's unused. Suppress entries with
`@SuppressWarnings("unused")` in scope.

Do NOT use a regex sweep for unused imports - false positives are
guaranteed (reflective uses, javadoc refs, annotation processors, generated
code).

## FQN-in-javadoc - defer to `javadoc-normalize`

The `fqn-auto-import` rule in `javadoc-normalize` already handles
`{@link a.b.c.X}` -> `import a.b.c.X;` + `{@link X}` atomically. It also
flags conflicts (`fqn-skip`) when the simple name clashes with a local
declaration or existing import. Calling that skill is the right entry point
for any FQN-in-javadoc audit; do not duplicate the logic here.

## Sample workflow

1. `mcp__IntelliJ_IDE__search_regex` with `^import\s+(?:static\s+)?[\w.]+\.\*;`
   - report N wildcard imports, list top files.
2. For each file in scope, `mcp__IntelliJ_IDE__get_file_problems` to surface
   unused imports. Aggregate and report.
3. If the same files also have FQN javadoc refs, route to `javadoc-normalize`
   instead of fixing inline.

## Fallback

If IntelliJ is not attached:
- Wildcards: `Grep` with `^import\s+(?:static\s+)?[\w.]+\.\*;` and
  `glob: "**/*.java"`.
- Unused imports: no clean fallback. Surface this gap in your reply rather
  than guessing.

## After running

If the audit produced edits (auto-removed unused imports, expanded
wildcards), invoke `gradle-verify-gate`. Wildcards expanded in place can
introduce subtle compile drift in obscure overload cases.
