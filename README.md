# Toolsmith

A small, project-scoped **MCP server** that gives an AI coding agent typed, deterministic access to the Simplified Java workspace's most-repeated developer operations - running the module-scoped gradle gate, tallying JUnit results, reordering imports to IntelliJ's exact layout, and normalizing javadocs. Each becomes a single cheap tool call instead of a shell incantation the agent re-derives on every turn.

> [!IMPORTANT]
> This project is under active development. The tool surface and return shapes
> may change until a stable `1.0.0` release is published.

## Table of Contents

- [Why Toolsmith](#why-toolsmith)
- [Tools](#tools)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Registering with Claude Code](#registering-with-claude-code)
- [Usage](#usage)
  - [As MCP tools](#as-mcp-tools)
  - [As a command line](#as-a-command-line)
- [Import Ordering](#import-ordering)
- [Architecture](#architecture)
  - [Package Overview](#package-overview)
  - [Project Structure](#project-structure)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)
- [Acknowledgments](#acknowledgments)

## Why Toolsmith

Mining a large corpus of past agent sessions over this workspace showed the same handful of shell shapes being hand-rewritten hundreds of times - most prominently the gradle gate:

```bash
cd "W:/.../asset-renderer" && ./gradlew compileJava test -q 2>&1 \
    | grep -vE "incubating|warning" | tail -40 ; echo "EXIT: ${PIPESTATUS[0]}"
```

Every invocation re-derives the `cd` prefix, the noise filter, an arbitrary `tail -N`, and a fragile `${PIPESTATUS}` read (which a `grep | tail` pipeline silently corrupts). The JUnit result tally and a naive import sorter were likewise re-authored as throwaway scripts again and again.

Toolsmith replaces those with a few **stable, typed** tools. The tools carry real logic and are called constantly, so they earn their place; prompt-routing helpers (rename, symbol search) deliberately stay as lightweight skills. The server is **project-scoped** - it registers only under the Java workspace, so its schemas cost nothing in unrelated sessions.

## Tools

| Tool | What it does | Replaces |
|------|--------------|----------|
| `gradle_verify` | Runs module-scoped gradle tasks, captures the **true** exit code, strips known noise, surfaces the first real failure. | The `cd ... && ./gradlew ... \| grep -vE noise \| tail -N; echo PIPESTATUS` shape |
| `test_tally` | Parses `build/test-results/test/*.xml` into `{tests, passed, failed, errors, skipped, failing_tests[]}`. | The inline python/awk JUnit tallies |
| `reorder_imports` | Reorders Java imports to the IntelliJ **Default** layout, byte-for-byte. Idempotent; wildcard- and CRLF-safe. | Hand-written `sortimports.py` throwaways |
| `javadoc_normalize` | Audits or `--fix`es javadocs against the project conventions (block form, dashes, tags, FQN auto-import). | Manual javadoc sweeps |

## Getting Started

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| [Python](https://www.python.org/) | **3.10+** | 3.14 recommended (matches the workspace toolchain) |
| [FastMCP](https://pypi.org/project/fastmcp/) | **1.0+** | Installed automatically as a dependency |
| A JDK + Gradle wrapper | - | Only needed for `gradle_verify` / `test_tally`; each module ships its own `./gradlew` |

### Installation

Install into an isolated environment (recommended) or the workspace interpreter:

```bash
cd Simplified-Dev/toolsmith
pip install -e .
```

This puts a `toolsmith` executable on your `PATH` (the MCP entry point) and makes the `toolsmith.*` modules importable for command-line use.

### Registering with Claude Code

Toolsmith is meant to load **only** when working under the Java workspace. Copy the example registration to the workspace root and merge it into any existing `.mcp.json`:

```bash
cp .mcp.json.example /w/Workspace/Java/Simplified/.mcp.json
```

```json
{
  "mcpServers": {
    "toolsmith": {
      "command": "toolsmith",
      "env": { "SIMPLIFIED_ROOT": "W:/Workspace/Java/Simplified" }
    }
  }
}
```

Run `/mcp` in Claude Code to confirm `toolsmith` loads and lists its four tools.

## Usage

### As MCP tools

Once registered, the agent calls the tools directly. Representative shapes:

- `gradle_verify(module="ar")` - compile+test asset-renderer, module-scoped.
- `gradle_verify(module="persistence", compile_only=True)` - compile only.
- `test_tally(module="d4j")` - tally discord4j-framework's last test run.
- `reorder_imports(paths=["src/main/java"], check=True)` - report out-of-order files without writing.
- `javadoc_normalize(paths=["src/main/java/dev/simplified/Foo.java"], fix=True)`.

Module tokens accept a short alias (`ar`, `d4j`, `pers`, ...), a bare module name, or a path - see [`modules.py`](src/toolsmith/modules.py).

### As a command line

Every tool is also a standalone module, so the same logic works without the MCP layer:

```bash
python -m toolsmith.imports --check src/main/java     # gate: exit 1 if any file is out of order
python -m toolsmith.imports --diff  src/main/java     # preview
python -m toolsmith.imports         src/main/java     # rewrite in place
python -m toolsmith.tally  ar --fails 20              # tally asset-renderer
```

## Import Ordering

`reorder_imports` reproduces the IntelliJ **Default** scheme, which is authoritative for this codebase (no custom `IMPORT_LAYOUT_TABLE`, no `.editorconfig`). The layout is three blank-line-separated groups:

```
<all other non-static imports, ASCII-sorted>       group 1
                                                   (blank)
<javax.* ASCII-sorted, then java.* ASCII-sorted>   group 2
                                                   (blank)
<all static imports, ASCII-sorted>                 group 3
```

The subtle rules that a naive line sort gets wrong: `javax.*` precedes `java.*` (not alphabetical); sorting is flat-string ASCII (so an upper-cased class segment precedes a lower-cased sub-package at the same depth); only `java`/`javax` are special-cased (`jakarta.*`, `io.*`, `reactor.*`, ... are "other"); statics form one trailing group; wildcards are preserved verbatim. When the IntelliJ MCP is attached, prefer its live Optimize Imports; this reorderer is the faithful IDE-independent fallback.

## Architecture

### Package Overview

- `toolsmith.server` - the FastMCP stdio server; a thin typed veneer that forwards each tool to a library module.
- `toolsmith.gradle` - `gradle_verify`: module resolution, unpiped gradle run for a reliable exit code, noise/signal filtering.
- `toolsmith.tally` - `test_tally`: JUnit XML parsing and failing-name extraction.
- `toolsmith.imports` - `reorder_imports`: the IntelliJ-Default reorderer.
- `toolsmith.javadoc` - the bundled javadoc auditor/normalizer.
- `toolsmith.modules` - workspace root, module aliases, and directory resolution shared by the rest.

### Project Structure

```
src/toolsmith/    - server + one module per tool
tests/            - pytest suite (reorderer + tally)
pyproject.toml    - packaging + entry point (toolsmith = toolsmith.server:main)
.mcp.json.example - project-scoped registration to copy to the workspace root
```

## Development

```bash
pip install -e ".[dev]"
python -m pytest -q
```

The suite validates the reorderer's layout, `javax`-before-`java` ordering, idempotency, wildcard and CRLF handling, and the tally's counts and failing-name extraction.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). This project is part of the [Simplified-Dev](https://github.com/simplified-dev) ecosystem; issues and pull requests are welcome.

## License

Licensed under the [Apache License 2.0](LICENSE.md).

## Acknowledgments

Scaffolded with the assistance of Claude Code. Copyright remains with the Simplified project; the design was grounded in a measured audit of real workspace sessions rather than guesswork.
