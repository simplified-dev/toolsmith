# Toolsmith

A general-purpose **Java workspace dev toolkit for AI agents**, packaged as a Claude Code **plugin**. It discovers the modules in any Java workspace and gives the agent typed, deterministic tools - gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize, module lookup, JSON-against-DTO key coverage - as an MCP server, plus a bundle of Java refactor / move / audit **skills**.

Mining a large corpus of past agent sessions showed the same shapes hand-rewritten hundreds of times: the gradle noise-strip gate, the JUnit XML tally, a naive import sorter, guessed module paths. Each becomes one cheap, correct call instead of a shell incantation the agent re-derives every turn. Nothing is hardcoded to a particular checkout - point `toolsmith setup` at any Java workspace and it discovers the modules, their paths, base packages and short aliases into a cache the tools read.

> [!IMPORTANT]
> Under active development. Tool surface and return shapes may change until a stable `1.0.0`.

## Table of Contents

- [Install](#install)
  - [1. Install the package](#1-install-the-package)
  - [2. Set up your workspace](#2-set-up-your-workspace)
  - [3. Enable the plugin](#3-enable-the-plugin)
- [Features](#features)
  - [MCP tools](#mcp-tools)
  - [Skills](#skills)
  - [CLI](#cli)
- [Import ordering](#import-ordering)
- [Architecture](#architecture)
- [Contributing](#contributing) · [License](#license) · [Acknowledgments](#acknowledgments)

## Install

### 1. Install the package

From the plugin directory (isolated env recommended):

```bash
pip install .            # or: pipx install .   /   pip install -e .  for development
```

This puts a `toolsmith` executable on your `PATH` (the MCP entry point and the CLI).

### 2. Set up your workspace

Discover the modules once per workspace (writes `<root>/.toolsmith/modules.json` and registers the root):

```bash
toolsmith setup /path/to/your/java/workspace     # or run with no arg from the workspace root
```

Optional: pin custom short aliases in `<root>/.toolsmith/aliases.json` (`{"discord4j-framework": "d4j"}`), then re-run `setup`.

<details>
<summary>What the scan actually records, and how a root is resolved later</summary>

`setup` walks the root for build files (pruning `build/`, `.git/`, `cache/`, ...) and records one row per project:

- **`kind`** - the build system, from one marker file each: `build.gradle` / `build.gradle.kts`, `pom.xml`, `pyproject.toml` / `setup.py` / `setup.cfg`. First match wins, so a gradle project holding a `pyproject.toml` for its dev scripts stays gradle.
- **`repo`** - whether the directory is a git repository root. `kind` and `repo` are independent axes and differ in both directions: a gradle module can sit inside a repo it does not own, and a repo can carry no build file at all. A directory earns a row by either.
- **`package`** - the base Java package, computed from the single-child directory chain under `src/main/java`. Several roots do not match the directory name (`collections` -> `dev.simplified.collection`, `utils` -> `dev.simplified.util`, `gson-extras` -> `dev.simplified.gson`, `spring-framework` -> `dev.simplified.serverapi`), which is why `gradle_modules` exists: look them up, do not guess.
- **`shorthand`** - an auto-acronym alias, or your `aliases.json` override.
- **`buildable`** - a JVM source tree, not merely a `src/` directory. A python project has one of those too, and `java locate` would otherwise search it for `.java`.

The cache lands at `<root>/.toolsmith/modules.json`, and the root is registered in `~/.config/toolsmith/roots.json`. Every later command resolves the active root in this order: explicit argument -> `TOOLSMITH_ROOT` -> walking up from the cwd for a `.toolsmith` cache -> the registry default.

Re-run `setup` after adding a module. The cache carries no compatibility shim: one predating a field raises rather than defaulting.

</details>

### 3. Enable the plugin

Add this repo as a marketplace and install the plugin (activates the MCP server from `.mcp.json` **and** the bundled skills):

```
/plugin marketplace add /path/to/toolsmith
/plugin install toolsmith@toolsmith
```

Run `/mcp` to confirm the `toolsmith` server loaded. (Standalone alternative: skip the plugin and register the server yourself with a project `.mcp.json` containing `{"mcpServers":{"toolsmith":{"command":"toolsmith","args":["serve"]}}}` - the CLI and MCP work without the plugin; the plugin just also ships the skills.)

## Features

Every entry below is collapsed. They share one shape - **Needs**, **Parameters**, **Result**, **Tips**, **Also as** - whether the entry is an MCP tool, a skill or a shell command.

A name's prefix marks **who can use** it rather than what it happens to parse: `java` needs Java source, `gradle` needs a gradle build, `jitpack` reaches an external service, `branch` reaches git. The same four prefixes name the MCP tools, the CLI groups and the skills.

### MCP tools

<details>
<summary><b><code>gradle_modules</code></b> - the discovered project inventory</summary>

**Needs** a workspace that has been through `toolsmith setup`.

**Parameters** - none.

**Result**: `root`, `count`, and `modules[]` - each with `name`, `path` (workspace-relative), `kind`, `repo`, `package`, `shorthand`, `buildable`.

**Tips**

- This is the answer to "what is this module's base package", and several roots are counter-intuitive. Look them up here rather than guessing a path from the directory name.
- `kind` and `repo` are independent: a gradle module can sit inside a repo it does not own, and a repo can declare no build system at all (`kind` is then `null`).
- No inventory returns a `note` telling you to run `setup`, not an error.

**Also as** `toolsmith gradle modules`.

</details>

<details>
<summary><b><code>gradle_verify</code></b> - the module-scoped gradle gate, with the true exit code</summary>

**Needs** a gradle module.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `module` | `str` | *required* | alias (`ar`), name (`asset-renderer`), or path |
| `tasks` | `list[str]` | `compileJava test` | the gradle tasks to run |
| `tail` | `int` | `25` | how many de-noised trailing lines come back |
| `compile_only` | `bool` | `False` | use `compileJava compileTestJava` as the default task set |
| `rerun` | `bool` | `False` | force past up-to-date checks and the build cache (`--rerun-tasks`) |

**Result**: `module`, `tasks`, `root`, `exit_code`, `ok`, `first_failure`, `lines`, `lines_kind`. On a timeout `ok` is `False`, `exit_code` is `None` and `timed_out` is `True`.

**Tips**

- **Read `lines_kind` before reading `lines`.** `signal` means the lines matched as failure diagnostics. `tail` means nothing matched, so these are just the last few lines the build happened to print - often unrelated chatter from whatever it shelled out to, and *not* a summary. `empty` is normal for a clean `-q` run and is not evidence the build no-opped.
- `lines` is **never** a test tally. Counts printed there come from whatever the build spawned. Use `gradle_tally` for real numbers.
- `rerun` is what makes tests actually execute instead of restoring `FROM-CACHE`. A `clean` task does not do this.
- A module whose `kind` is not gradle is refused **before** the gradle-root walk, because that walk goes *up*: a python project under a directory holding a `gradlew` would otherwise resolve a sibling project's wrapper and run a different build.

**Also as** `toolsmith gradle verify`, and the `gradle-verify-gate` skill.

</details>

<details>
<summary><b><code>gradle_tally</code></b> - JUnit XML to counts plus the failing test names</summary>

**Needs** a gradle module that has already run its tests.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `module` | `str` | *required* | alias, name, or path whose `test-results` to tally |
| `subdir` | `str` | `""` | sub-path holding a nested build dir |
| `fails` | `int` | `15` | cap on the failing testcase names returned |

**Result**: `classes`, `tests`, `passed`, `skipped`, `failures`, `errors`, `failing_tests[]`, `found`, `ok`, `module`.

**Tips**

- Replaces the recurring grep / awk / python one-liners over `build/test-results/test/*.xml`. Never hand-roll that again.
- The XML is whatever the last run left behind. If the tests restored from cache, the numbers are from an *earlier* run - `gradle_verify(rerun=True)` first.
- `found: false` (with a `note`) means there is no XML at all, which is different from zero tests.

**Also as** `toolsmith gradle tally`.

</details>

<details>
<summary><b><code>java_reorder_imports</code></b> - imports to the IntelliJ Default layout, byte for byte</summary>

**Needs** Java source files. No project model, no IDE, no build.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `paths` | `list[str]` | *required* | `.java` files, directories, or globs |
| `check` | `bool` | `False` | report what *would* change and write nothing |

**Result**: `scanned`, `changed` / `would_change`, `skipped`, `errors`, and `details[]` (per file: `path`, `status`, `reason`).

**Tips**

- Idempotent, and faithful to Optimize Imports - wildcards and CRLF/LF survive untouched. The exact layout is in [Import ordering](#import-ordering).
- `check` is the gate form: it sets no files, and a run with pending changes is the failure signal.
- When IntelliJ is actually attached, prefer its live Optimize Imports. This is the faithful IDE-independent fallback.

**Also as** `toolsmith java reorder`.

</details>

<details>
<summary><b><code>java_docs_normalize</code></b> - audit or fix javadocs against the project conventions</summary>

**Needs** Java source files.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `paths` | `list[str]` | *required* | `.java` files, directories, or globs |
| `fix` | `bool` | `False` | apply the safe auto-fixes in place; otherwise audit only |
| `scope` | `str` | `"all"` | one of `class`, `method`, `field`, `all` |
| `prefix` | `list[str]` | `[]` | extra FQN top-level prefixes to auto-import, additive to the defaults |

**Result**: `{fixed, exit_code, output}` - `output` is the human report, last 8000 characters.

**Tips**

- `fix: true` is **destructive**. Re-Read any file you had already Read before editing it further.
- Two buckets, deliberately: safe transforms are applied, and anything whose right phrasing is semantic (FQN refs, `Gets` / `Returns` prefixes on field-like docs, missing `@param`) is only *flagged* for review.
- `package-info.java` is detected and exempted from the import-the-link-target rule - it keeps inline FQN refs and carries no imports.

**Also as** `toolsmith java docs`, and the `java-docs-normalize` skill.

</details>

<details>
<summary><b><code>java_json_diff</code></b> - which wire keys the DTO tree does not bind</summary>

**Needs** a Java source root and a JSON capture. No gradle build, no network, no IDE.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `json_path` | `str` | *required* | the JSON capture to audit |
| `src` | `str` | *required* | Java source root whose classes are parsed |
| `root` | `str` | *required* | the class the walk starts from, by simple or nested name |
| `node` | `str` | `""` | dotted path narrowing the document to one subtree |
| `union` | `str` | `""` | path expression whose matches merge into one template first; `[]` is every array element, `{}` every object value |
| `section` | `str \| None` | `None` | keep only this top-level key of the node |
| `show_mapped` | `bool` | `False` | also return the keys a field *does* map (large - use `cap`) |
| `show_unresolved` | `bool` | `False` | also return object-valued fields whose Java type did not parse |
| `max_depth` | `int` | `12` | how deep the parallel walk descends |
| `cap` | `int` | `200` | rows per returned key list; `0` is uncapped |
| `opaque` | `list[str]` | `[]` | types the walk never descends into; an entry written `-Name` is *removed* from the built-in set |
| `map_types` | `list[str]` | `[]` | collection types whose **last** type argument describes a JSON object's values |
| `seq_types` | `list[str]` | `[]` | collection types whose **first** type argument describes an element |
| `wrapper_types` | `list[str]` | `[]` | types unwrapped to the type inside them |
| `strict` | `list[str]` | `[]` | strictness switches: `names`, `extract`, `capture`, `collapse`, `flatten`, or `all` |
| `phantom` | `bool` | `False` | also project both sides and report the fields no JSON key reaches |
| `fail_on_phantom` | `bool` | `False` | let that second direction decide `ok` and the exit code |

**Result**: the inputs echoed, then `ok`, the counts `unmapped` / `mapped` / `unresolved` / `classes`, `sections[]` (each with a capped `keys` list of path + kind), `shape_mismatches`, and the diagnostics `empty_classes`, `ambiguous_types`, `unreadable_files` - none of which is a finding about the JSON. Under `phantom`, three more: `phantoms`, `phantom_total`, and `projection` holding both whole sorted line lists uncapped.

**Tips**

- **What it understands is a vocabulary, not a language.** `@SerializedName`, `@SerializedPath`, `@Extract`, `@Capture`, `@Collapse`, `@Key` and `@Flatten` are read; `@Lenient`, `@Split` and `@Fallback` are inert. The Java is parsed with line regexes, so a field declaration split across two lines matches *nothing* - which is what `empty_classes` reports.
- **A key optional in every individual sample reports unmapped from one sample and mapped from a union of them.** Pass `union` to merge the samples into one template before the walk.
- **The strictness switches all default off**, and each is a fix. Each can change the verdict on a capture the walk currently calls clean, so each is asked for by name - a report you have calibrated against does not move underneath you.
- **`ok` is more than `unmapped == 0`.** A sequence field bound to a JSON object is a `shape_mismatch`: it cannot decode at all (`Expected BEGIN_ARRAY but was BEGIN_OBJECT`), and it is the one finding that names no key.
- **`phantom` answers the opposite question** and most of its answer is a subtree the account simply did not send. It also puts both whole projections into the return uncapped, so ask for it deliberately.
- Key lists carry `cap` rows while the counts beside them are whole - `truncated` is what says a list is short. Note the default differs by surface: `200` here, uncapped on the CLI, because a shell caller pipes to a file.

**Also as** `toolsmith java json_diff`, which additionally offers `--format`, `--rows` and `--open`. There is no `open` parameter here: the editor hand-off opens a diff viewer on somebody's screen, and an agent has none.

</details>

<details>
<summary><b><code>jitpack_status</code></b> - is this commit built? Read-only, triggers nothing</summary>

**Needs** a module published through JitPack, and network.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `module` | `str` | *required* | alias (`d4j`), name, or path |
| `refs` | `list[str]` | `[]` | git refs or short shas; empty asks about `origin/HEAD` |

**Result**: `module`, `group`, `artifact`, `org`, `repo`, `repo_dir`, `records`, `counts` (total / ok / error / in-flight / unknown), and `refs[]` - one entry per requested ref with `ref` (7-char sha), `full`, `source`, `pushed`, `status`, `state`, `ok`. `ok` is `True` only when every requested ref is built.

**Tips**

- **Reads exactly one URL** - the versionless build list - so it is safe to call freely. Run it *before* `jitpack_build`.
- **Never** call the per-version `/api/builds/<group>/<artifact>/<version>` endpoint yourself: it silently *starts a build*, and its records then go stale for months.
- Empty `refs` means `origin/HEAD`, never the local `HEAD`. A local commit nobody pushed is not a thing JitPack can build.
- A precondition failure - module unresolved, no git remote, an unresolvable / unpushed / ambiguous ref, or a module that publishes to Maven Central - sets a top-level `error` instead of counts.

**Also as** `toolsmith jitpack status`.

</details>

<details>
<summary><b><code>jitpack_build</code></b> - trigger and wait for exactly one build</summary>

**Needs** a module published through JitPack, network, and a pushed commit. **One invocation is one real build on a third-party service.**

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `module` | `str` | *required* | alias, name, or path |
| `ref` | `str` | `""` | git ref or short sha; empty resolves `origin/HEAD` |
| `timeout` | `float` | `480.0` | seconds to hold the blocking request |
| `force` | `bool` | `False` | re-request a sha the precheck already reported |

**Result**: `module`, `group`, `artifact`, `org`, `repo`, `ref`, `full_sha`, `source`, `precheck`, `action`, `url`, `http_code`, `elapsed`, `log_tail`, `hints`, `ok`, and `status` - one of `built`, `already-built`, `failed`, `cached-failure`, `timeout`, `in-flight`, `symbolic`, `precondition`, `error`. A success also carries `pin`, the ready-to-paste `strictly(...)` line.

**Tips**

- **`timeout` is inconclusive, not a failure.** The build continues server-side; calling again *attaches to the same build* rather than starting a second one (`resume` is `True`). The default sits below a typical harness cap so a slow build returns a clean dict instead of killing the call - the CLI's default is `900` because a shell has no such cap.
- **`cached-failure` never changes for the same sha** - not with `force`, not on retry. It needs a **new commit**.
- **A list record saying `ok` is not a verdict.** The list reports `ok` for artifacts that answer 404, so green here means an HTTP 200 on the `.pom` and nothing less.
- **A green build is a compile check.** JitPack builds with `-xtest`, and composite `includeBuild` substitution means a green consumer build proves nothing. Use `gradle_verify` for a real gate.

**Also as** `toolsmith jitpack build`, which additionally offers `--log-lines` and `--allow-symbolic`.

</details>

<details>
<summary><b><code>jitpack_set</code></b> - rewrite one artifact's pin across every build file that declares it</summary>

**Needs** a workspace with JitPack pins. **Destructive when `status` is `written`.**

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `artifact` | `str` | *required* | artifact id (`collections`), or `<group>:<artifact>` when one id is pinned under two groups |
| `sha` | `str` | *required* | the pin to write, cut to 7 chars; with verification on, any ref local git resolves |
| `modules` | `list[str]` | `[]` | narrow the edit to these modules; empty means every module that pins it |
| `check` | `bool` | `False` | report what would change and write nothing |
| `no_verify` | `bool` | `False` | skip the JitPack precheck |
| `include_snapshots` | `bool` | `False` | also nail `<branch>-SNAPSHOT` coordinates to the sha |

**Result**: `artifact`, `group`, `sha`, `check`, `changed`, `unchanged`, `skipped`, `files[]` (per site: file, line, before, after, changed, module, form), `skipped_sites[]` each with a `reason`, `verification`, `ok`, and `status` - one of `written`, `checked`, `unchanged`, `unbuilt`, `unverified`, `write-failed`, `precondition`.

**Tips**

- **Zero matches is an error, not a silent no-op.** A `sed` that matches nothing exits 0 and prints nothing, so a typo'd artifact is discovered days later when a build resolves the old sha. Here the error carries the artifact ids that *were* found. Treat a non-zero exit as "the edit did not happen".
- **Matching is exact**, unlike the substring filter `jitpack pins` audits with. A rewrite that over-matches edits the wrong artifact.
- **What gets written is the sha verification resolved, not your spelling.** Every prefix length is a separate JitPack build, so pinning 8 or 40 chars asks for an artifact nobody produced. Read `sha` back off the result.
- Both dialects are handled - `strictly(...)` and `group:artifact:version`, including a `strictly` that wrapped onto the next line - and each site keeps whichever it already used. CRLF survives, because a rewrite is an offset splice rather than a pattern replacement.
- `-SNAPSHOT` coordinates are skipped by default: they float onto the new commit by themselves, so nailing one to a sha is a semantic change.

**Also as** `toolsmith jitpack set`.

</details>

<details>
<summary><b><code>jitpack_order</code></b> - what has to be re-pinned after a sha changes, in order</summary>

**Needs** a workspace with JitPack pins. Offline - it walks the pin graph, not JitPack.

**Parameters**

| Name | Type | Default | What it is |
|---|---|---|---|
| `artifact` | `str` | *required* | the artifact whose sha is changing |

**Result**: `artifact`, `chain` (the flat order, source first), `order[]` (per artifact: `artifact`, `modules`, `depth`, `reason`, `repins` - the concrete declaration sites to edit), `floating`, `cycles`, `total`, `direct`, `cascade`, `note`, `ok`.

**Tips**

- **`strictly` is not transitive here.** Published gradle module metadata for these artifacts records `{"requires": "<sha>"}`, so an inherited pin is a *soft* constraint that a consumer's own `strictly()` overrides. Do not tell anyone gradle forces the whole chain to move.
- Hence the two reasons: **`direct`** means the module pins this artifact itself, so leaving it stale keeps it building against the old code. **`cascade`** means it only pins things that get a new sha as a result - re-pinning is this workspace's one-sha-per-artifact convention.
- `-SNAPSHOT` consumers appear under `floating`, not in the order: they resolve the new commit with no edit at all, so they earn no commit of their own and do not propagate.
- The order is deterministic (depth, then alphabetical) but not unique; any topological order of the same graph is equally valid.

**Also as** `toolsmith jitpack order`, whose entry carries the full three-command re-pin recipe.

</details>

> [!NOTE]
> `jitpack pins` and `branch finish` are **CLI-only** and are not MCP tools. `pins` is a wide human table; `branch finish` pushes, opens a pull request and merges it, so it stays something a human invokes rather than something an agent reaches for on its own initiative.

### Skills

The plugin ships these so they travel with it. They stay skills rather than MCP tools because a routing skill carries no logic and would only cost per-turn schema tokens. All of them are `auto_invoke: true` - the **Parameters** below are what you say or hand over, not flags, except where a skill ships a real script.

<details>
<summary><b><code>java-file-mover</code></b> - move or rename a <code>.java</code> file with everything that follows it</summary>

**Needs** Java source. IntelliJ MCP when attached; it degrades without.

**Parameters**

| Input | What it is |
|---|---|
| the file or class | what is moving - "move `X` to package `Y`", "put `X` in module `Z`", "rename class `X`" |
| the destination | a package, a directory, or another module |

**Result**: the file moved with its `package` statement, every import of it, git history, and cross-module dependents all updated, then reordered and compile-gated.

**Tips**

- **Three cases, and they are not the same job.** A rename in place, a move within one module, and a move to a *different* module - which crosses a git repo boundary here - each have their own sequence. The cross-repo one is where history and dependent build files come in.
- Routes type-aware work to IntelliJ `rename_refactoring`, which does the whole move atomically. The fallback is a deterministic `git mv` + package-rewrite + import-fix + reorder pipeline.
- `git mv` invalidates a prior Read: the file has a new path, so re-Read there before editing.
- Delegates rather than duplicating: symbol discovery to `java-symbol-search` / `java-find-usages`, import order to `toolsmith java reorder`, javadoc and FQN fixes to `java-docs-normalize`, the compile gate to `gradle-verify-gate`.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-bulk-rename</code></b> - rename a package, class, method or field across a codebase</summary>

**Needs** Java source. IntelliJ MCP for the type-aware path.

**Parameters**

| Input | What it is |
|---|---|
| the symbol | the package, class, method or field being renamed |
| the new name | what it becomes |

**Result**: imports, usages and package directory layout updated atomically.

**Tips**

- Auto-invoked **before** a `find ... -exec sed -i` over `.java` files, or batched Edits across more than one Java file. That is the failure mode it exists to intercept.
- `sed` survives only for genuine text-only swaps - a comment token, a log message - where no type information is involved.
- After an IntelliJ refactor your Read of a touched file is stale. Re-Read before editing it.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-symbol-search</code></b> - find declarations, throw sites, extends / implements, imports</summary>

**Needs** Java source. IntelliJ MCP for AST-aware results.

**Parameters**

| Input | What it is |
|---|---|
| the pattern | a symbol name, `throw new X`, `extends X`, `implements X`, `import a.b.c`, or a call site |

**Result**: structured matches from `search_symbol`, `search_regex`, `find_files_by_glob` and `get_symbol_info`.

**Tips**

- Auto-invoked **before** a Grep for those patterns, because a text match cannot tell a declaration from a mention in a comment.
- Falls back to Grep when the IDE is not attached, which is a real answer rather than a refusal.
- Declaration lookup lives here; *usage* lookup is a separate entry point, `java-find-usages`.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-find-usages</code></b> - who calls this, who reads this field</summary>

**Needs** Java source. IntelliJ MCP for the real Find Usages engine.

**Parameters**

| Input | What it is |
|---|---|
| the symbol | the method, class or field to find references to |

**Result**: callers, references and readers / writers, from the same engine Find Usages runs in the IDE.

**Tips**

- Run it **before** deleting a symbol or changing a signature. That is the whole point of splitting it out from `java-symbol-search`.
- "Where is X used" and "where is X declared" are different questions with different tools; asking the wrong one is how a deletion looks safe.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-import-audit</code></b> - wildcards, unused imports, FQN javadoc refs</summary>

**Needs** Java source. IntelliJ MCP for the unused-import inspection.

**Parameters**

| Input | What it is |
|---|---|
| the scope | the files, module or changeset to audit |

**Result**: a triage of wildcard imports, unused imports, and FQN refs that should be imported.

**Tips**

- Unused-import detection uses the IDE's own inspection (`get_file_problems`) rather than a heuristic - a regex cannot see through javadoc references and annotations.
- FQN-in-javadoc handling is **deferred to `java-docs-normalize`**, which already auto-imports. Do not do it twice.
- A good pre-commit sweep for a change that touched many `.java` files.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-docs-normalize</code></b> - the javadoc auditor, as a skill</summary>

**Needs** Java source.

**Parameters**

| Input | What it is |
|---|---|
| the paths | files, directories or globs to audit |
| fix or audit | whether to apply the safe transforms |

**Result**: fixes applied for the mechanical conventions, and a flag list for everything whose right phrasing is semantic.

**Tips**

- Invoked **before** any manual javadoc edit across one or more Java files - hand-editing is where the conventions drift.
- Auto-fixes: block form, `--` and em dashes, `@author` / `@since`, trailing periods, column-aligned `@param`s.
- Flags only: FQN refs, `Gets` / `Returns` prefixes on field-like docs, missing `@param` / `@return`.
- `package-info.java` is the documented exception - inline FQN links, no imports - and is handled as such.

**Also as** the `java_docs_normalize` MCP tool and `toolsmith java docs`.

</details>

<details>
<summary><b><code>java-record-audit</code></b> - record component docs against the convention</summary>

**Needs** Java source containing records.

**Parameters**

| Input | What it is |
|---|---|
| the scope | the records, files or PR to audit |

**Result**: flagged `@param` lines, surfaced for review.

**Tips**

- The rule it checks is easy to get backwards: a record `@param` describes what the component **is**, not what to **pass**.
- Also flags `Gets ` / `Returns ` prefixes on component descriptions and missing component docs.
- **Never auto-rewrites.** The right phrasing is semantic, so it surfaces and stops.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-modifier-audit</code></b> - <code>final</code>, <code>static</code> and visibility drift</summary>

**Needs** Java source. IntelliJ MCP for the IDE's own inspections.

**Parameters**

| Input | What it is |
|---|---|
| the scope | the classes, module or release to audit |

**Result**: candidates flagged, never applied.

**Tips**

- Routes to `get_file_problems` for `CanBeFinal`, `MutableStaticField` and friends, plus targeted `search_regex` for what the inspections do not surface.
- **Conservative by design**: it never applies a modifier. A `final` that looks safe can break a subclass in another module.
- Useful before a release that wants leaf classes locked down.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>java-exception-class-gen</code></b> - a conforming exception class from the template</summary>

**Needs** Java source. No MCP - it is a pure template.

**Parameters**

| Input | What it is |
|---|---|
| the class name | e.g. `ProfileParseException` |
| root or child | whether it extends `RuntimeException` or an existing root exception |

**Result**: the class with five constructors in the fixed order, the right annotations, and the canonical javadoc shape.

**Tips**

- The trap it removes: a **root** reverses the `super()` argument order (`super(message, cause)`), a **child** passes through (`super(cause, message)`). Getting that backwards compiles fine and swaps the two at runtime.
- Annotations are part of the contract: `@NotNull` on `cause` / `message`, `@PrintFormat` on format strings, `@Nullable` on `Object... args`.
- Auto-invoked when a new `extends RuntimeException` is about to be written or pasted, which is earlier than it sounds - that is before the wrong shape exists.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>gradle-verify-gate</code></b> - the standard verification gate after a change lands</summary>

**Needs** a gradle module.

**Parameters**

| Input | What it is |
|---|---|
| the module | which module to gate |
| the tasks | defaults to `compileJava test` |

**Result**: a pass / fail verdict with the first failure surfaced cleanly.

**Tips**

- Auto-invoked after `java-bulk-rename`, after `java-exception-class-gen` drops a file, after Edit batches over `.java` files, and at any `Phase N -> verify` boundary a plan declares.
- Prefers module-scoped `:module:compileJava :module:test` over a full `./gradlew build`, which is the difference between seconds and minutes.
- Skips redundant re-runs when nothing has changed, so a plan can declare the gate at every boundary without paying for it every time.

**Also as** the `gradle_verify` MCP tool and `toolsmith gradle verify`.

</details>

<details>
<summary><b><code>java-jmh-regression-gate</code></b> - go / no-go on a benchmark change</summary>

**Needs** Java benchmarks and two captured JMH outputs. It **never re-runs** the benchmarks.

**Parameters** - this one ships a real script, `skills/java-jmh-regression-gate/compare.py BASELINE CANDIDATE`:

| Flag | Default | What it is |
|---|---|---|
| `BASELINE`, `CANDIDATE` | *required* | gradle-redirect text files, or JMH JSON (`--rf json`); the format is auto-detected |
| `--threshold N` | `2.0` | regression bound in percent |
| `--top N` | `10` | how many worst regressions to print |
| `--ignore PATTERN` | - | regex on the benchmark name to exclude, repeatable |
| `--format text\|json\|md` | `text` | `md` produces the Benchmark / Size / Baseline / Final / Delta table |
| `--require-pairs` | off | fail when a benchmark exists in one file but not the other |

**Result**: benchmarks matched 1-to-1 by `(name, mode, params)`, each categorised `regression` / `wash` / `improvement`, and a non-zero exit when any regression exceeds the threshold.

**Tips**

- **The halt is advisory.** The gate exits non-zero and shows the worst regressions; whether to bisect or accept is a human call.
- It is a `java-*` skill even though it reads gradle's stdout, because only a Java project has benchmarks to gate. The prefix names who can use a thing, not what it parses.
- Document the reason inline whenever you reach for `--ignore` - a silently excluded benchmark is a regression nobody will find later.

**Also as** nothing - this is a skill only.

</details>

<details>
<summary><b><code>branch-finish</code></b> - the end-of-branch ritual, routed to the CLI</summary>

**Needs** git, `gh`, and a branch that is not the base.

**Parameters**

| Input | What it is |
|---|---|
| the repository | optional; the current directory otherwise |
| the intent | "finish this branch", "land this branch", "open a PR and merge it", "merge and clean up" |

**Result**: it routes to `toolsmith branch finish` rather than hand-running the eight git and `gh` commands.

**Tips**

- **Never on Claude's own initiative.** It pushes, opens and merges, so the user decides when it runs. That is the reason it is a CLI command wrapped by a skill instead of an MCP tool.
- What the skill buys over hand-running the sequence: base-branch detection, resuming a ritual that died half way, the ancestry assertion after the merge, and `git branch -d` rather than `-D`.

**Also as** `toolsmith branch finish`, whose entry carries the flags and the full ritual.

</details>

<details>
<summary><b><code>transcript-mine</code></b> - distill past sessions into ranked artifacts</summary>

**Needs** Claude Code JSONL transcripts under `~/.claude/projects`.

**Parameters**

| Input | What it is |
|---|---|
| `PREFIX` | the encoded project-dir prefix, covering all sub-projects |
| `OUT` | a scratch directory for the artifacts |

**Result**: tool-use frequency, the full shell-command corpus plus deduped shapes and a verb histogram, Edit / Read / Write / Grep input histograms, and the avoidable-error histogram.

**Tips**

- **Runs `rg` in the Bash tool shell, never in a nested script.** `rg` is a shell *function* here, so a child `bash script.sh` cannot use it - and `grep -oP` errors on this locale.
- Exclude the session you are running in, or the meta-session dominates its own results.
- This is the measurement that justified most of this repo. Run it before designing a new workflow or skill, so the design is grounded in what actually happens rather than what feels frequent.

**Also as** nothing - this is a skill only.

</details>

### CLI

```bash
toolsmith setup [ROOT]               # discover + cache a workspace's modules
toolsmith serve                      # run the stdio MCP server (what the plugin launches)
toolsmith java locate TypeRegistrar  # find a class file across module sources
toolsmith java reorder --check src   # import order gate (or without --check to rewrite)
toolsmith java docs --fix src        # javadoc audit / fix
toolsmith java json_diff --json capture.json --src src/main/java/pkg --root Member
toolsmith gradle modules             # print the cached inventory
toolsmith gradle verify ar test      # module-scoped gradle gate (alias or name)
toolsmith gradle tally d4j           # JUnit tally
toolsmith jitpack status d4j         # are the module's commits built (read-only)
toolsmith jitpack build d4j          # trigger + wait for one build; prints the strictly(<sha>) pin
toolsmith jitpack pins               # workspace pin-drift table (commits behind / unbuilt / stale)
toolsmith jitpack order coll         # what to re-pin after collections changes, in order
toolsmith jitpack set coll SHA       # rewrite that pin everywhere
toolsmith branch finish [ar]         # push, open the PR, merge it, pull the base, delete the branch
```

Two conventions hold across every command. **Facts go to stdout, commentary to stderr** - hints, log tails, notices and diagnostics never land in a piped stdout. And **the last stdout line is the verdict**, `GATE:`, `JITPACK:` or `BRANCH:`, with `java json_diff --format human` the one documented exception.

<details>
<summary><b><code>toolsmith setup [ROOT]</code></b> - discover a workspace and cache its inventory</summary>

**Needs** a directory holding projects. Run once per workspace, and again after adding a module.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `ROOT` | `.` | the workspace root to scan |

**Result**: the scan summary, one line per project (`shorthand`, `kind`, git marker, path, package), and the cache path. Exit `0`.

**Tips**

- Writes `<root>/.toolsmith/modules.json` and registers the root in `~/.config/toolsmith/roots.json`. Everything else reads that cache.
- Alias overrides live in `<root>/.toolsmith/aliases.json` and take effect on the next `setup`.
- CLI-only, like `java locate` and `jitpack pins`: it is a wide table for a human, and an agent wants `gradle_modules`.

**Also as** nothing - this bootstraps the rest and belongs to no subject group.

</details>

<details>
<summary><b><code>toolsmith serve</code></b> - run the stdio MCP server</summary>

**Needs** nothing beyond the install. This is what `.mcp.json` launches.

**Parameters** - none.

**Result**: a FastMCP stdio server named `toolsmith`, speaking the tools in [MCP tools](#mcp-tools).

**Tips**

- You rarely type this. The plugin runs it; so does a project `.mcp.json` containing `{"mcpServers":{"toolsmith":{"command":"toolsmith","args":["serve"]}}}`.
- `/mcp` in Claude Code is how you confirm it loaded.

**Also as** nothing - like `setup`, it belongs to no subject group.

</details>

<details>
<summary><b><code>toolsmith java locate NAME</code></b> - find a class file across module sources</summary>

**Needs** a cached workspace with Java source.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `NAME` | *required* | class name, with or without the `.java` suffix |

**Result**: one absolute path per hit on stdout. Exit `0` with hits, `1` with none, `2` with no inventory.

**Tips**

- Searches the `src` tree of **buildable** modules only, so a python project's `src/` is not swept for `.java`.
- Use it before a guessed Read. A wrong guess costs a failed tool call and a re-derivation; this costs one command.
- CLI-only. An agent with the IntelliJ MCP attached has `find_files_by_name_keyword`, and the Glob tool covers the rest.

**Also as** nothing - CLI-only by design.

</details>

<details>
<summary><b><code>toolsmith java reorder PATHS...</code></b> - imports to the IntelliJ Default layout</summary>

**Needs** Java source files.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `PATHS...` | *required* | `.java` files, directories, or globs |
| `--check` | off | report what would change and write nothing |

**Result**: one line per changed file, then a `scanned / reordered / skipped / errors` summary. Exit `0` clean, `1` when `--check` finds pending changes or any file errored, `2` when no `.java` file was found at all.

**Tips**

- The `2` matters: "nothing to do" and "you pointed me at the wrong directory" are different answers, and only one of them is a passing gate.
- Idempotent and byte-faithful - see [Import ordering](#import-ordering) for the exact layout.
- `--check` is the pre-commit form.

**Also as** the `java_reorder_imports` MCP tool.

</details>

<details>
<summary><b><code>toolsmith java docs PATHS...</code></b> - audit or fix javadocs</summary>

**Needs** Java source files.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `PATHS...` | *required* | `.java` files, directories, or globs |
| `--fix` | off | apply the safe transforms in place |
| `--scope` | `all` | one of `class`, `method`, `field`, `all` |
| `--prefix` | - | extra FQN top-level prefix to auto-import, repeatable and additive |

**Result**: per-file `fix` and `flag` lines, then a `scanned / with findings / fixes / flags` summary. Exit `0`, or `1` when no `.java` file was found.

**Tips**

- **This is an audit, not a gate.** Findings do not change the exit code - read the summary line, not `$?`.
- `--fix` rewrites files. Re-Read anything you had open before editing it further.
- Flags are the deliberate half: FQN refs, `Gets` / `Returns` prefixes on field-like docs and missing `@param` are surfaced rather than guessed at.

**Also as** the `java_docs_normalize` MCP tool and the `java-docs-normalize` skill.

</details>

<details>
<summary><b><code>toolsmith java json_diff</code></b> - audit a JSON capture against the DTOs that bind it</summary>

**Needs** a Java source root and a JSON file. No gradle build, no network.

**Parameters** - every MCP parameter of `java_json_diff`, spelled as a flag (`--json`, `--src`, `--root`, `--node`, `--union`, `--section`, `--show-mapped`, `--show-unresolved`, `--max-depth`, `--strict`, `--opaque`, `--map-type`, `--seq-type`, `--wrapper-type`, `--phantom`, `--fail-on-phantom`), plus four that exist only here:

| Name | Default | What it is |
|---|---|---|
| `--format` | detected | `agent`, `human`, `gate`, `diff`, or `json` |
| `--cap N` | `0` (uncapped) | rows per key list - the shell default differs from the MCP default of `200`, because a shell caller pipes to a file |
| `--rows N` | `50` | findings the `agent` report prints before deferring the rest; `0` prints every one |
| `--open` | off | write both projections to files and open them in IntelliJ's diff viewer (implies `--phantom`) |

**Result**: the report in the chosen format. Exit `0` when every key maps, `1` for a red gate (unmapped keys, or a shape mismatch), `2` when the audit never ran.

```bash
toolsmith java json_diff --json capture.json --src src/main/java/pkg --root Member
toolsmith java json_diff ... --union 'profiles.[].members.{}'   # merge every sample first
toolsmith java json_diff ... --show-mapped                      # the whole binding table
toolsmith java json_diff ... --phantom                          # the reverse direction
```

**Tips**

- **The `1` / `2` split is the point.** `1` says the classes do not cover the wire. `2` says an unreadable capture, a source root that parsed no classes, an unknown `--root` or a path expression matching nothing - the audit never ran, and reporting that as "the classes cover nothing" is the worst available reading.
- **`--format` always beats the detection, and `gate` has a name you can type.** Detection is `gate` under CI, `agent` under an agent harness or a pipe, `human` on a terminal - but a default that flips silently under a pipe makes a red CI run impossible to reproduce by hand, so reproducing one means asking for the format CI used.
- **`--format human` ends without a verdict line**, alone among these commands: its body is verified byte for byte, so a line appended to stdout would be a change to an interface. `gate` prints nothing at all on a pass and one stderr line on a red run, stdout empty either way, so a job piping stdout to an artifact gets an empty file.
- **`--section` is refused together with a projection.** A section narrows the capture and not the class graph, so every field outside it would read as a phantom of the narrowing.
- **`--open` is CLI-only and refuses to launch under `CI` or `TOOLSMITH_NO_LAUNCH`** (it still writes the files). The editor is found via `TOOLSMITH_IDEA`, then `<root>/.toolsmith/editor.json`, then `PATH`, then per-platform globs; a failure names the env var and what it searched.
- Colour follows the published `NO_COLOR` rule - present **and non-empty** disables it - so `NO_COLOR=` is how you un-set one you inherited.

**Also as** the `java_json_diff` MCP tool, which returns the walk's dict unrendered.

</details>

<details>
<summary><b><code>toolsmith gradle modules</code></b> - print the cached inventory</summary>

**Needs** a workspace that has been through `setup`.

**Parameters** - none.

**Result**: one line per project - shorthand, kind, name, base package. Exit `0`, or `2` with no inventory.

**Tips**

- This is where you check a base package instead of guessing it from a directory name.
- A `2` means run `setup`, not that the workspace is empty.

**Also as** the `gradle_modules` MCP tool.

</details>

<details>
<summary><b><code>toolsmith gradle verify MODULE [TASKS...]</code></b> - the module-scoped gate</summary>

**Needs** a gradle module.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `MODULE` | *required* | alias, name, or path |
| `TASKS...` | `compileJava test` | gradle tasks to run |
| `--tail N` | `25` | de-noised trailing lines to print |
| `--compile-only` | off | use `compileJava compileTestJava` instead |
| `--rerun` | off | force past up-to-date and the build cache (`--rerun-tasks`) |

**Result**: the signal lines, then `GATE: PASS rc=0` or `GATE: FAIL rc=N`. Exit `0` pass, `1` fail, `2` precondition.

**Tips**

- Replaces `cd MODULE && ./gradlew ... 2>&1 | grep -vE incubating | tail -N; echo PIPESTATUS`, which loses the true exit code. Do not hand-roll it.
- When no failure diagnostics matched, the printed lines are just a tail, and a `-- trailing build output, not a summary --` line says so **on stderr** so a piped stdout stays clean.
- `--rerun` is what makes tests actually run rather than restoring `FROM-CACHE`.
- A non-gradle module is refused before the gradle-root walk, because that walk goes up and would find a sibling project's wrapper.

**Also as** the `gradle_verify` MCP tool and the `gradle-verify-gate` skill.

</details>

<details>
<summary><b><code>toolsmith gradle tally MODULE</code></b> - the JUnit result tally</summary>

**Needs** a gradle module whose tests have already run.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `MODULE` | *required* | alias, name, or path |
| `--fails N` | `15` | cap on the failing test names printed |

**Result**: one `classes= tests= passed= skipped= failures= errors=` line, then a `FAIL <name>` line per failing test. Exit `0` green, `1` with failures or errors, `2` when there is no XML.

**Tips**

- Never write an inline python or awk over `build/test-results/test/*.xml` again. This is that, correctly.
- The XML is from the *last* run. If it restored from cache, re-run with `gradle verify --rerun` first.

**Also as** the `gradle_tally` MCP tool.

</details>

<details>
<summary><b><code>toolsmith jitpack status TARGET</code></b> - are these commits built, read-only</summary>

**Needs** network and a JitPack-published module.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `TARGET` | *required* | module alias, name, or path |
| `--ref REF` | `origin/HEAD` | git ref or short sha, repeatable |
| `--timeout N` | `20` | seconds before the list read is abandoned |

**Result**: the coordinates, one line per ref, then `JITPACK: OK` / `RED` / `UNKNOWN` / `PRECONDITION`. Exit `0` / `1` / `2`.

**Tips**

- **Read-only.** One versionless list endpoint, never a trigger. Ask this before pinning anything.
- `UNKNOWN` is its own word for a reason: the list never answered, so no ref was scored. `RED` there would read as "this sha needs building", which is a different and more expensive claim.
- Never call `/api/builds/<group>/<artifact>/<version>` by hand - it silently starts a build.

**Also as** the `jitpack_status` MCP tool.

</details>

<details>
<summary><b><code>toolsmith jitpack build TARGET</code></b> - trigger and wait for one build</summary>

**Needs** network, a JitPack-published module, and a pushed commit. **This starts a real build on a third-party service.**

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `TARGET` | *required* | module alias, name, or path |
| `--ref REF` | `origin/HEAD` | git ref or short sha, **at most one** |
| `--timeout N` | `900` | seconds to hold the blocking request |
| `--log-lines N` | `60` | trailing `build.log` lines printed on a failure |
| `--force` | off | re-request a sha the precheck already reported |
| `--allow-symbolic` | off | permit a `<branch>-SNAPSHOT` ref instead of a sha |

**Result**: the coordinates, the precheck, the outcome line, the `pin:` line on success, and `JITPACK: BUILT` / `OK` / `BUILDING` / `TIMEOUT` / `FAILED` / `PRECONDITION`. Exit `0` / `1` / `2`.

**Tips**

- **`TIMEOUT` is inconclusive, not a failure**, and the verdict line says `re-run to attach`: the build continues server-side and a second invocation joins it rather than starting another.
- **`FAILED (cached-failure)` needs a new commit.** Not a retry, not `--force`.
- Two `--ref`s is exit `2`, deliberately: one invocation is one build, so the plural would be ambiguous about how many builds you just paid for.
- The `build.log` tail is labelled on stderr, like the gradle tail, so it is not misread as a summary.
- A green build is a **compile** check - JitPack builds with `-xtest`. Gate locally with `gradle verify`.

**Also as** the `jitpack_build` MCP tool (whose default timeout is `480`, below a typical harness cap).

</details>

<details>
<summary><b><code>toolsmith jitpack pins [ARTIFACT]</code></b> - the workspace pin-drift table</summary>

**Needs** network and a workspace with JitPack pins. Read-only.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `ARTIFACT` | every pin | case-insensitive **substring** filter |
| `--timeout N` | `20` | seconds before a list read is abandoned |
| `--max-behind N` | - | fail when a pin is more than N commits behind its default branch |

**Result**: a table of `artifact / pin / behind / split / jitpack / consumers`, a counts line, then `JITPACK: OK` / `STALE` / `RED` / `UNKNOWN`. Exit `0` / `1` / `2`.

**Tips**

- **Staleness alone is never red.** Most pins are stale, so a default-red gate would be noise. What flips it is a pin JitPack cannot serve, or an explicit `--max-behind`.
- **`split` is the column to look at.** `strictly()` is gradle's hardest constraint, so one artifact pinned at two shas is a conflict waiting to fail a resolve. Only a real split gets a number; `1` everywhere would bury the ones that matter.
- Two numbers, two words: `rows` is distinct `(artifact, pin)` pairs, `occurrences` is declaration sites. They are not the same count.
- The filter here is a **substring**, where `jitpack set` matches exactly. A filter that over-matches shows extra rows; a rewrite that over-matches edits the wrong artifact.
- CLI-only - a wide human table, not a call in an agent loop.

**Also as** nothing - CLI-only by design.

</details>

<details>
<summary><b><code>toolsmith jitpack set ARTIFACT SHA</code></b> - rewrite a pin across the workspace</summary>

**Needs** a workspace with JitPack pins, and network unless `--no-verify`. **Destructive.**

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `ARTIFACT` | *required* | artifact id, or `<group>:<artifact>` when one id is pinned under two groups |
| `SHA` | *required* | the sha to pin, cut to 7 chars; with verification on, any ref git resolves |
| `--module M` | every module | narrow the edit, repeatable |
| `--check` | off | report what would change and write nothing |
| `--no-verify` | off | skip the check that the sha is pushed and built |
| `--include-snapshots` | off | also nail `<branch>-SNAPSHOT` coordinates |
| `--timeout N` | `20` | seconds before the verification read is abandoned |

**Result**: one `file:line  before -> after` row per site, a counts line, then `JITPACK: SET` / `CHECK` / `OK` / `RED` / `UNKNOWN` / `FAILED` / `PRECONDITION`. Exit `0` / `1` / `2`.

**Tips**

- **Zero matches is a precondition error carrying the ids that do exist**, not a silent success. That is the whole reason this exists instead of a `sed`.
- **The id is compared with `==` and never reaches a pattern**, so an id holding a regex metacharacter cannot widen the match, and the rewrite is an offset splice, so CRLF survives.
- **What lands in the file is the sha verification resolved**, not what you typed - every prefix length is a separate JitPack build.
- The file is re-read strictly before writing and each span re-checked, so a file that moved under you is refused rather than corrupted.
- Idempotent: a site already holding the sha reports `==` and is not rewritten, and a file with nothing to change is not written at all.

**Also as** the `jitpack_set` MCP tool.

</details>

<details>
<summary><b><code>toolsmith jitpack order ARTIFACT</code></b> - the re-pin cascade, in dependency order</summary>

**Needs** a workspace with JitPack pins. Offline, no network.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `ARTIFACT` | *required* | the artifact whose sha is changing |

**Result**: the arrow chain, a `# / depth / artifact / reason / re-pin` table, a counts line, then `JITPACK: ORDER N module(s) after <artifact>`. Exit `0` / `1` / `2`.

A multi-module re-pin is `order` to plan it, then `set` and `build` one module at a time along that chain:

```bash
toolsmith jitpack order collections     # -> collections -> utils -> reflection -> ...
# then, one module at a time, in that order:
toolsmith jitpack set collections <sha> --module utils
toolsmith gradle verify utils           # gate the edit locally
git -C ../utils commit -am "..."        # then push
toolsmith jitpack build utils           # -> the new sha for the next step
```

**Tips**

- **`strictly` is not transitive**, so do not say gradle forces the chain to move. Published module metadata records `{"requires": "<sha>"}` - an inherited pin is soft, and a consumer's own `strictly()` overrides it.
- **`direct` is the one that must move**: the module declares the changed artifact, so a stale pin keeps it on the old code. **`cascade`** is this workspace's one-sha-per-artifact convention.
- `-SNAPSHOT` consumers land under `floating`: no edit, so no commit, so no propagation.
- The arrow chain is the line worth pasting into a plan.

**Also as** the `jitpack_order` MCP tool.

</details>

<details>
<summary><b><code>toolsmith branch finish [MODULE]</code></b> - push, open the PR, merge, clean up</summary>

**Needs** git, `gh`, and a branch that is not the base. **CLI-only and never an MCP tool** - it pushes, opens and merges, so the user decides when it runs.

**Parameters**

| Name | Default | What it is |
|---|---|---|
| `MODULE` | the cwd | module shorthand, module name, or a path inside the repository |
| `--title` | last commit subject | pull request title |
| `--body-file FILE` | composed | file to use as the PR body |
| `--base BRANCH` | detected | override the base-branch detection |
| `--delete-remote` | **off** | also delete the remote branch |
| `--no-merge` | off | push and open the PR, then stop for review |
| `--dry-run` | off | print the ordered plan and mutate nothing |
| `--yes` | off | merge without asking; required when stdin is not a terminal |
| `--squash`, `--rebase` | refused | declared only so asking gets the reason rather than a usage error |

**Result**: the repo / branch / base header, one line per step with its state and the actual command, then `BRANCH: FINISHED` / `OPENED` / `PLAN` / `DECLINED` / `FAILED` / `PRECONDITION`, ending `<base>@<sha>`. Exit `0` / `1` / `2`.

```bash
toolsmith branch finish --dry-run          # the plan, mutating nothing
toolsmith branch finish                    # prompts before the merge
toolsmith branch finish ar                 # name the repository instead of standing in it
toolsmith branch finish --no-merge         # push + open the PR, stop for review
toolsmith branch finish --yes --delete-remote
```

**Tips**

- **The remote branch is not deleted.** Local cleanup and remote cleanup are separate decisions, so `git branch -d` runs and the remote branch stays until you pass `--delete-remote`.
- **A merge needs an answer.** With a terminal it prompts; without one it refuses before mutating anything unless `--yes` is passed, so an unattended run cannot merge by accident. `--no-merge` needs no confirmation, because no merge is planned.
- **The merge is a merge commit, and a squash or rebase is refused rather than honoured.** Commits here are often independently gated units, and flattening them destroys the per-commit revert granularity that gating produced.
- **The post-merge check is ancestry, never sha equality.** A true merge leaves a merge commit at the base tip, so `rev-parse <base> == rev-parse <branch>` is false on *every* successful merge. What holds is `git merge-base --is-ancestor <branch-sha> <base>`, asked about a sha captured before the checkout. The delete is `git branch -d`, never `-D`, which refuses a branch the base does not contain and is the backstop if that check is ever wrong.
- **The verdict ends `master@<sha>`** - the base tip after the pull, which is what a revert of this landing starts from. The merge step cannot supply it: at that point the merge exists only on the remote. It is `None` on a dry run and on any failure before the pull.
- **Re-running resumes rather than repeats.** A branch origin already carries, a PR already open for the head, a PR already merged - each is detected and reported as skipped. Every precondition is established before the first push, so a refusal changes nothing.
- **The base branch is detected**, from `refs/remotes/origin/HEAD` then `gh repo view`. Nothing assumes `master` or `main`.
- **A token that resolves to neither a known module nor a directory is refused**, rather than falling back to the current directory and finishing whatever branch the shell was sitting on. Discovery scans for *gradle* modules, so a repository that is not one - toolsmith itself - is named by path or by cwd, never by alias.

**Also as** the `branch-finish` skill, which routes here.

</details>

## Import ordering

The layout `java_reorder_imports` reproduces is IntelliJ's **Default** scheme, and it is worth stating in full because it is not alphabetical and not what a naive sorter produces:

1. All other non-static imports, ASCII sort.
2. *blank line*
3. `javax.*`, then `java.*` - in that order, which is not alphabetical.
4. *blank line*
5. All static imports.

Only `java` and `javax` are special-cased. Wildcards are preserved rather than expanded, CRLF and LF line endings each survive as they were, and the transform is idempotent - running it twice changes nothing the first run did not.

Prefer the live IntelliJ Optimize Imports when the IDE is attached. This is the faithful IDE-independent fallback, and the reason it is worth having is that a hand-rolled sorter gets step 3 wrong every time.

## Architecture

```
.claude-plugin/   plugin.json + marketplace.json (plugin + single-plugin marketplace)
.mcp.json         registers the `toolsmith serve` MCP server
skills/           the bundled Java skills
hooks/            PreToolUse advisory - nudges shell symbol/declaration greps toward the Grep tool / symbol-search (non-blocking; silent on import-greps and non-Java)
src/toolsmith/
  cli.py          the `toolsmith` command (subcommands + serve)
  server.py       FastMCP server (thin veneer over the modules below)
  discovery.py    scan + cache + root resolution
  modules.py      cache-backed module/alias/package lookup
  gradle.py · tally.py · imports.py · javadoc.py · jitpack.py · branch.py · json_diff.py   one module per tool
tests/            pytest suite (discovery, reorderer, tally, jitpack, branch, json_diff)
```

Each tool's real logic lives in its own library module; `server.py` stays a thin typed veneer that forwards to it, which is what keeps every tool usable from the shell and testable without the MCP layer.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the code style, and how to add a tool. Part of the [Simplified-Dev](https://github.com/simplified-dev) ecosystem.

## License

[Apache License 2.0](LICENSE.md).

## Acknowledgments

Scaffolded with Claude Code, grounded in a measured audit of real workspace sessions. Copyright remains with the Simplified project.
