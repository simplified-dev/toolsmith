---
name: gradle-verify-gate
description: Run the standard Gradle verification gate (`./gradlew compileJava test`) over a Gradle module after a refactor phase, file rename, or exception migration. Auto-invoked after `java-bulk-rename`, after `java-exception-class-gen` drops a new file, after Edit batches touching `.java` files, or whenever a multi-phase plan declares a `Phase N -> verify` boundary. Prefers module-scoped `./gradlew :module:compileJava :module:test` over full `./gradlew build`; surfaces only the first failure cleanly; skips redundant re-runs when nothing has changed. Also the route for a filtered (`--tests`), forced (`--rerun`) or `-D`-property test run: `toolsmith gradle verify` / `gradle_verify` places each where gradle binds it.
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
| One test class, a forced re-run, or a run needing a system property | `toolsmith gradle verify MODULE :test --tests "*FooTest" --rerun -D key=value` |

## Filtered, forced and property-carrying runs

Run them through the tool rather than the wrapper - it keeps the true exit
code, and it places each argument where gradle reads it:

```bash
toolsmith gradle verify MODULE :test --tests "*FooTest" --tests "Bar.baz" --rerun -D key=value
```

or `gradle_verify(module, tasks=[":test"], tests=["*FooTest"], rerun=True,
system_properties=["key=value"])` over MCP.

- **`--tests` and `--rerun` bind to the LAST task.** Gradle binds a task option
  to the task written immediately before it, so the tool writes them straight
  after the last task given and they apply to that task alone. Name the task
  they are for last. With no task given they bind to `test`; under
  `--compile-only` with no task given they are refused, since that default set
  has no test task.
- The tool cannot see a task's type: `--tests` on a task that is not a `Test`
  task fails in gradle, and the gate reports gradle's reason as its first
  failure - `Problem configuring task :compileJava from command line. >
  Unknown command-line option '--tests'.` - so check the task named last.
- **Tasks are relative to the module.** A module without a wrapper of its own
  (`tooling` under `asset-renderer`) has each task scoped by the project path
  its directory spells under the gradle root, with a leading `:` dropped
  first: `toolsmith gradle verify tooling :test` runs `:tooling:test`, not the
  root's `:test`. Run a root task by naming the root as the module.
- `--rerun` re-runs the last task alone; `--rerun-tasks` re-runs the whole
  graph, compilation included. Only the first is needed to stop a test task
  restoring `FROM-CACHE`.
- `-D KEY=VALUE` is repeatable and has no position. A property is no task input
  unless the build declares one, so pair it with `--rerun` when a test must see
  a new value. A bare `-D KEY` is refused: gradle reads it as the empty string,
  which `Boolean.getBoolean` reads as `false`.
- The CLI prints the command gradle received on stderr (`gradlew <args>`), and
  the MCP result carries it as `argv` - read it to see where an option landed.

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
- Gradle's reason under `* What went wrong:` for a failure that printed
  neither - an unknown task, an option the task does not take, a test filter
  that matched nothing. The gate's `first_failure` carries it.

If there are multiple failures, report the count and show the first one;
direct the user to the full log only if they ask. Avoid dumping the entire
gradle output.

## Cross-reference

This is the follow-up gate that `java-bulk-rename` and
`java-exception-class-gen` both ask Claude to invoke. The natural pipeline:

`java-symbol-search` -> `java-bulk-rename` (or `java-exception-class-gen`) -> `gradle-verify-gate` -> commit.
