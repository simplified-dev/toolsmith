---
name: gradle-verify-gate
description: Run the standard Gradle verification gate (`./gradlew compileJava test`) over a Gradle module after a refactor phase, file rename, or exception migration. Auto-invoked after `java-bulk-rename`, after `java-exception-class-gen` drops a new file, after Edit batches touching `.java` files, or whenever a multi-phase plan declares a `Phase N -> verify` boundary. Prefers module-scoped `./gradlew :module:compileJava :module:test` over full `./gradlew build`; surfaces only the first failure cleanly; skips redundant re-runs when nothing has changed.
auto_invoke: true
tags: [java, gradle, build, verification, ci]
---

# gradle-verify-gate

Run the canonical Java verification gate after a refactor phase. Prefers
module-scoped tasks to keep cycle time low; only escalates to a root build
when the change crosses module boundaries.

## When to invoke

- Immediately after `java-bulk-rename` completes.
- Immediately after `java-exception-class-gen` drops a new class into the
  project.
- After any Edit / Write batch that touches `.java` files.
- At every `Phase N -> verify` gate in a multi-phase plan.
- Before any commit that includes Java changes.

## Standard invocation

```bash
./gradlew :module:compileJava :module:test
```

Prefer module-scoped tasks over the root `./gradlew build`. The full build
recompiles every module and runs every test; module-scoped tasks finish in a
fraction of the time and still catch the failure modes that matter for a
local edit.

## Decision rules

| Situation | Command |
|---|---|
| Single file edited in one module | `./gradlew :module:compileJava :module:test` |
| Multiple files in one module | `./gradlew :module:compileJava :module:test` |
| Cross-module rename or package move | `./gradlew build` (root) |
| Phase boundary in a multi-phase plan | Always invoke before commit; scope matches the phase |
| Compile-only sanity check with IDE attached | Prefer `mcp__IntelliJ_IDE__get_file_problems` or `mcp__IntelliJ_IDE__build_project` - cheaper than gradle |

## Skip when

- No `.java` file has been written or edited since the last successful gate
  run.
- The last gate run timestamp is newer than the most recent `Edit` / `Write`
  in this session.
- User says "skip verification" or "I'll run gradle myself".

These are heuristics for your judgment, not automated checks. When in doubt,
run the gate.

## Failure reporting

Surface only the first compile error or first failing test:

- File + line + message for compile errors.
- Test class + method + assertion message for test failures.

If there are multiple failures, report the count and show the first one;
direct the user to the full log only if they ask. Avoid dumping the entire
gradle output.

## Cross-reference

This is the follow-up gate that `java-bulk-rename` and
`java-exception-class-gen` both ask Claude to invoke. The natural pipeline:

`java-symbol-search` -> `java-bulk-rename` (or `java-exception-class-gen`) -> `gradle-verify-gate` -> commit.
