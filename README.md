# Toolsmith

A general-purpose **Java workspace dev toolkit for AI agents**, packaged as a Claude Code **plugin**. It discovers the modules in any Java workspace and gives the agent typed, deterministic tools - gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize, module lookup - as an MCP server, plus a bundle of Java refactor / move / audit **skills**. Each becomes one cheap, correct call instead of a shell incantation the agent re-derives every turn.

> [!IMPORTANT]
> Under active development. Tool surface and return shapes may change until a stable `1.0.0`.

## Table of Contents

- [What you get](#what-you-get)
- [Install](#install)
  - [1. Install the package](#1-install-the-package)
  - [2. Set up your workspace](#2-set-up-your-workspace)
  - [3. Enable the plugin](#3-enable-the-plugin)
- [MCP tools](#mcp-tools)
- [Bundled skills](#bundled-skills)
- [CLI](#cli)
  - [Moved spellings](#moved-spellings)
- [Finishing a branch](#finishing-a-branch)
- [Re-pinning a cascade](#re-pinning-a-cascade)
- [How discovery works](#how-discovery-works)
- [Import ordering](#import-ordering)
- [Architecture](#architecture)
- [Development](#development)
- [Contributing](#contributing) · [License](#license) · [Acknowledgments](#acknowledgments)

## What you get

Mining a large corpus of past agent sessions showed the same shapes hand-rewritten hundreds of times - the gradle noise-strip gate, the JUnit XML tally, a naive import sorter, guessed module paths. Toolsmith replaces them with stable, typed tools and a self-configuring module map. Nothing is hardcoded to a particular checkout: point `toolsmith setup` at any Java workspace and it discovers the modules, their paths, base packages, and short aliases into a cache the tools read.

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

### 3. Enable the plugin

Add this repo as a marketplace and install the plugin (activates the MCP server from `.mcp.json` **and** the bundled skills):

```
/plugin marketplace add /path/to/toolsmith
/plugin install toolsmith@toolsmith
```

Run `/mcp` to confirm the `toolsmith` server loaded. (Standalone alternative: skip the plugin and register the server yourself with a project `.mcp.json` containing `{"mcpServers":{"toolsmith":{"command":"toolsmith","args":["serve"]}}}` - the CLI and MCP work without the plugin; the plugin just also ships the skills.)

## MCP tools

A prefix names **who can use** the tool: `gradle_*` needs a gradle build, `java_*` acts on Java source files, `jitpack_*` reaches JitPack.

| Tool | What it does |
|------|--------------|
| `gradle_modules` | The discovered inventory: each project's name, path, **kind**, git-repo flag, **base package**, short alias. Look packages up instead of guessing - several roots are counter-intuitive. |
| `gradle_verify` | Module-scoped gradle tasks with the **true** exit code, noise stripped, first failure surfaced. |
| `gradle_tally` | `build/test-results/test/*.xml` -> `{tests, passed, failed, errors, skipped, failing_tests[]}`. |
| `java_reorder_imports` | Java imports to the IntelliJ **Default** layout, byte-for-byte. Idempotent; wildcard- and CRLF-safe. |
| `java_docs_normalize` | Audit or `--fix` the javadocs in Java source against the project conventions. |
| `jitpack_status` | Is a module's commit built on JitPack? One read of the versionless build list - **never** triggers a build. |
| `jitpack_build` | Precheck, then trigger and wait for **one** build of a sha. Returns the verdict, the ready-to-paste `strictly(...)` pin, and the failing `build.log` tail. |
| `jitpack_set` | Rewrite one artifact's pin across every build file that declares it. Exact match, both dialects, sha verified before writing, idempotent - and zero matches is an **error**, not a silent no-op. |
| `jitpack_order` | What has to be re-pinned after an artifact's sha changes, in dependency order. Offline graph walk; separates a pin that must move from one that moves by convention. |

## Bundled skills

The plugin ships the Java skills so they travel with it (they stay skills, not MCP tools - routing skills carry no logic and would only cost per-turn schema tokens):

- **java-file-mover** - move/relocate/rename a `.java` file with package statement, imports, git history, and cross-module deps all handled (delegates reorder to `toolsmith java reorder`, gate to `gradle-verify-gate`).
- **transcript-mine** - distill past session transcripts into ranked artifacts (tool/command frequency, error histograms).
- **java-bulk-rename**, **java-symbol-search**, **java-find-usages** - route renames/searches to IntelliJ MCP.
- **java-import-audit**, **java-modifier-audit**, **java-record-audit** - convention audits.
- **java-exception-class-gen** - generate a conforming exception class.
- **branch-finish** - the end-of-branch ritual (wraps `toolsmith branch finish`), so the eight git/gh steps are one command instead of hand-rolled.
- **gradle-verify-gate** (wraps `toolsmith gradle verify`), **java-docs-normalize** (wraps `toolsmith java docs`), **java-jmh-regression-gate**.

## CLI

Every tool is also a shell subcommand. A group names **who can use** what it holds: `java` needs Java source, `gradle` needs a gradle build, `jitpack` reaches an external service and `branch` reaches git. `setup` and `serve` stay top-level - they bootstrap the rest.

```bash
toolsmith setup [ROOT]               # discover + cache a workspace's modules
toolsmith gradle modules             # print the cached inventory
toolsmith gradle verify ar test      # module-scoped gradle gate (alias or name)
toolsmith gradle tally d4j           # JUnit tally
toolsmith java reorder --check src   # import order gate (or without --check to rewrite)
toolsmith java docs --fix src        # javadoc audit / fix
toolsmith java locate TypeRegistrar  # find a class file across module sources
toolsmith jitpack status d4j         # are the module's commits built on JitPack (read-only)
toolsmith jitpack build d4j          # trigger + wait for one build; prints the strictly(<sha>) pin
toolsmith jitpack pins               # workspace pin-drift table (commits behind / unbuilt / stale)
toolsmith jitpack order coll         # what to re-pin after collections changes, in order
toolsmith jitpack set coll SHA       # rewrite that pin everywhere (--check, --module, --no-verify)
toolsmith branch finish [ar]         # push, open the PR, merge it, pull the base, delete the branch
toolsmith serve                      # run the stdio MCP server (what the plugin launches)
```

### Moved spellings

Six subcommands moved under the two umbrellas. Each old spelling still runs, prints a one-line notice on stderr naming its replacement, and is hidden from `--help`:

| Deprecated | Current |
|---|---|
| `toolsmith modules` | `toolsmith gradle modules` |
| `toolsmith verify` | `toolsmith gradle verify` |
| `toolsmith tally` | `toolsmith gradle tally` |
| `toolsmith locate` | `toolsmith java locate` |
| `toolsmith reorder` | `toolsmith java reorder` |
| `toolsmith javadoc` | `toolsmith java docs` |

## Finishing a branch

`toolsmith branch finish` is the end-of-branch ritual as one command: push, write the PR body to a file, `gh pr create`, `gh pr merge --merge`, check out the base, pull it, confirm it really contains the branch, and delete the local branch. Run `--dry-run` first to see the ordered plan.

```bash
toolsmith branch finish --dry-run          # the plan, mutating nothing
toolsmith branch finish                    # prompts before the merge
toolsmith branch finish ar                 # name the repository instead of standing in it
toolsmith branch finish --no-merge         # push + open the PR, stop for review
toolsmith branch finish --yes --delete-remote
```

- **The repository is named the way every other command names one**: a module shorthand, a module name, or a path, resolved through the workspace's `.toolsmith/modules.json` and `aliases.json`. With no argument it reads the current directory. A token that resolves to neither a known module nor a directory is refused, rather than falling back to the current directory and finishing whatever branch the shell was sitting on. Discovery records every git repository root, so a repository carrying no build file is nameable too.
- **The merge is a merge commit.** `--squash` and `--rebase` exist only to be refused with the reason: commits here are often independently gated units, and flattening them destroys the per-commit revert granularity that gating produced.
- **It says where the base landed.** The verdict line ends `master@<sha>`, read after the pull - the sha a revert of the landing starts from, which the merge step cannot know because at that point the merge exists only on the remote.
- **The post-merge check is ancestry, not equality.** A true merge leaves a merge commit at the base tip, so `rev-parse <base> == rev-parse <branch>` is false on *every* successful merge; what holds is `git merge-base --is-ancestor <branch-sha> <base>`, asked about a sha captured before the checkout. The delete is `git branch -d`, never `-D`, so an unmerged branch is refused even if that check is ever wrong.
- **The base branch is detected**, from origin's head and then `gh repo view` - nothing assumes `master` or `main`.
- **Re-running resumes.** A branch origin already carries, a PR already open for the head, a PR already merged: each is detected and reported as skipped rather than redone.
- **A merge needs an answer.** With a terminal it asks; without one it refuses before mutating anything unless `--yes` is passed.
- **CLI only, deliberately.** It pushes, opens and merges, so it is not an MCP tool: the user decides when it runs.

## Re-pinning a cascade

`pins` reads, `order` plans, `set` writes. A multi-module re-pin is the three in sequence:

```bash
toolsmith jitpack order collections     # -> collections -> utils -> reflection -> ...
# then, one module at a time, in that order:
toolsmith jitpack set collections <sha> --module utils
toolsmith gradle verify utils           # gate the edit locally
git -C ../utils commit -am "..."        # then push
toolsmith jitpack build utils           # -> the new sha for the next step
```

`set` matches the artifact id **exactly** (`pins` filters by substring; a rewrite that over-matches edits the wrong artifact), handles both the `strictly(...)` and `group:artifact:version` forms plus a `strictly` that wrapped onto a later line, and preserves whichever a site already used. It refuses to write unless the sha resolves in the publishing repo's git, is pushed, and has a green JitPack build - `--no-verify` opts out. `--check` reports the diff and writes nothing. `-SNAPSHOT` coordinates are left floating unless `--include-snapshots`.

`order` is offline. It marks a module **direct** when it declares the changed artifact itself - its own `strictly()` binds, so a stale one keeps it on the old code - and **cascade** when it only pins things that get a new sha as a result. The difference matters because published module metadata records `{"requires": "<sha>"}`, not `strictly`: an inherited pin is a *soft* constraint that a consumer's own `strictly()` overrides, so a full cascade is this workspace's one-sha-per-artifact convention rather than something Gradle demands.

## How discovery works

`toolsmith setup` walks the root for `build.gradle*` (pruning `build/.git/cache/...`), computes each module's base Java package from its `src/main/java` chain, assigns a short alias (auto-acronym, or your `aliases.json` override), and writes `<root>/.toolsmith/modules.json` plus a `~/.config/toolsmith/roots.json` registry. The server and CLI resolve the active root by: explicit arg -> `TOOLSMITH_ROOT` env -> walking up from the cwd -> the registry default. Re-run `setup` after adding a module.

## Import ordering

`java_reorder_imports` reproduces the IntelliJ **Default** scheme: group 1 = all other non-static (ASCII sort), blank, group 2 = `javax.*` then `java.*` (not alphabetical), blank, group 3 = all static. Only `java`/`javax` are special-cased; wildcards and CRLF/LF are preserved; idempotent. Prefer the live IntelliJ MCP Optimize Imports when attached; this is the faithful IDE-independent fallback.

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
  gradle.py · tally.py · imports.py · javadoc.py · jitpack.py · branch.py   one module per tool
tests/            pytest suite (discovery, reorderer, tally, jitpack, branch)
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Part of the [Simplified-Dev](https://github.com/simplified-dev) ecosystem.

## License

[Apache License 2.0](LICENSE.md).

## Acknowledgments

Scaffolded with Claude Code, grounded in a measured audit of real workspace sessions. Copyright remains with the Simplified project.
