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

| Tool | What it does |
|------|--------------|
| `list_modules` | The discovered inventory: each module's name, path, **base package**, short alias. Look packages up instead of guessing - several roots are counter-intuitive. |
| `gradle_verify` | Module-scoped gradle tasks with the **true** exit code, noise stripped, first failure surfaced. |
| `test_tally` | `build/test-results/test/*.xml` -> `{tests, passed, failed, errors, skipped, failing_tests[]}`. |
| `reorder_imports` | Java imports to the IntelliJ **Default** layout, byte-for-byte. Idempotent; wildcard- and CRLF-safe. |
| `javadoc_normalize` | Audit or `--fix` javadocs against the project conventions. |
| `jitpack_status` | Is a module's commit built on JitPack? One read of the versionless build list - **never** triggers a build. |
| `jitpack_build` | Precheck, then trigger and wait for **one** build of a sha. Returns the verdict, the ready-to-paste `strictly(...)` pin, and the failing `build.log` tail. |

## Bundled skills

The plugin ships the Java skills so they travel with it (they stay skills, not MCP tools - routing skills carry no logic and would only cost per-turn schema tokens):

- **java-file-mover** - move/relocate/rename a `.java` file with package statement, imports, git history, and cross-module deps all handled (delegates reorder to `toolsmith reorder`, gate to `gradle-verify-gate`).
- **transcript-mine** - distill past session transcripts into ranked artifacts (tool/command frequency, error histograms).
- **java-bulk-rename**, **java-symbol-search**, **java-find-usages** - route renames/searches to IntelliJ MCP.
- **java-import-audit**, **java-modifier-audit**, **java-record-audit** - convention audits.
- **java-exception-class-gen** - generate a conforming exception class.
- **gradle-verify-gate** (wraps `toolsmith verify`), **javadoc-normalize** (wraps `toolsmith javadoc`), **jmh-regression-gate**.

## CLI

Every tool is also a shell subcommand (folding the former `gw` / `jtally` / `locate-java` helpers):

```bash
toolsmith setup [ROOT]          # discover + cache a workspace's modules
toolsmith modules               # print the cached inventory
toolsmith verify ar test        # module-scoped gradle gate (alias or name)
toolsmith tally d4j             # JUnit tally
toolsmith reorder --check src   # import order gate (or without --check to rewrite)
toolsmith javadoc --fix src     # javadoc audit / fix
toolsmith locate TypeRegistrar  # find a class file across module sources
toolsmith jitpack status d4j    # are the module's commits built on JitPack (read-only)
toolsmith jitpack build d4j     # trigger + wait for one build; prints the strictly(<sha>) pin
toolsmith jitpack pins          # workspace pin-drift table (commits behind / unbuilt / stale)
toolsmith serve                 # run the stdio MCP server (what the plugin launches)
```

## How discovery works

`toolsmith setup` walks the root for `build.gradle*` (pruning `build/.git/cache/...`), computes each module's base Java package from its `src/main/java` chain, assigns a short alias (auto-acronym, or your `aliases.json` override), and writes `<root>/.toolsmith/modules.json` plus a `~/.config/toolsmith/roots.json` registry. The server and CLI resolve the active root by: explicit arg -> `TOOLSMITH_ROOT` env -> walking up from the cwd -> the registry default. Re-run `setup` after adding a module.

## Import ordering

`reorder_imports` reproduces the IntelliJ **Default** scheme: group 1 = all other non-static (ASCII sort), blank, group 2 = `javax.*` then `java.*` (not alphabetical), blank, group 3 = all static. Only `java`/`javax` are special-cased; wildcards and CRLF/LF are preserved; idempotent. Prefer the live IntelliJ MCP Optimize Imports when attached; this is the faithful IDE-independent fallback.

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
  gradle.py · tally.py · imports.py · javadoc.py · jitpack.py   one module per tool
tests/            pytest suite (discovery, reorderer, tally)
notes/            provenance: the token-optimization audit that produced this
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

Scaffolded with Claude Code, grounded in a measured audit of real workspace sessions (see `notes/`). Copyright remains with the Simplified project.
