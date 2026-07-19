# toolsmith

Project-scoped MCP server exposing the Simplified Java workspace's deterministic dev tools (gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize). Python / FastMCP.

## Module Structure
- `toolsmith.server` - FastMCP stdio server; registers the four tools, forwards each to a library module
- `toolsmith.gradle` - `gradle_verify` (module-scoped gradle gate, reliable exit code, noise/signal filter)
- `toolsmith.tally` - `test_tally` (parse `build/test-results/test/*.xml`)
- `toolsmith.imports` - `reorder_imports` (IntelliJ Default layout reorderer)
- `toolsmith.javadoc` - bundled javadoc auditor/normalizer (the former `normalize.py`)
- `toolsmith.modules` - workspace root, module aliases, directory resolution (shared)

## Key Entry Points
- `toolsmith.server:main` - the packaged `toolsmith` executable (stdio MCP server)
- `python -m toolsmith` - same server without the console script
- `python -m toolsmith.imports [--check|--diff] PATH...` - reorderer CLI
- `python -m toolsmith.tally MODULE [--fails N]` - tally CLI

## Tools (MCP)
- `gradle_verify(module, tasks?, tail=25, compile_only=False)` -> `{exit_code, ok, first_failure, lines}`
- `test_tally(module, subdir="", fails=15)` -> `{tests, passed, skipped, failures, errors, ok, failing_tests[]}`
- `reorder_imports(paths[], check=False)` -> `{scanned, changed, would_change, skipped, errors, details[]}`
- `javadoc_normalize(paths[], fix=False, scope="all", prefix?[])` -> `{fixed, exit_code, output}`

## Import Order (what reorder_imports reproduces)
IntelliJ **Default** scheme (this codebase has no custom `IMPORT_LAYOUT_TABLE`, no `.editorconfig`):
group 1 = all other non-static (ASCII-sorted); blank; group 2 = `javax.*` then `java.*`; blank; group 3 = all static. Flat-string ASCII sort; only `java`/`javax` special-cased; wildcards and CRLF/LF preserved; idempotent. Prefer the live IntelliJ MCP Optimize Imports when attached; this is the IDE-independent fallback.

## Build / Test
```bash
pip install -e ".[dev]"
python -m pytest -q
```

## Conventions
- Python 3.10+ (workspace runs 3.14). Type hints on public functions; Google-style docstrings ("Args:"/"Returns:"). PEP 8.
- The javadoc rules in the user global CLAUDE.md govern Java sources this tool EDITS, not this Python source.

## Info
- Package: `toolsmith`, version `0.1.0`, license Apache-2.0
- Deps: `fastmcp>=1.0` (+ `pytest` for dev)
- Registered via a project-scoped `.mcp.json` at the workspace root (see `.mcp.json.example`)
- `SIMPLIFIED_ROOT` env var overrides the workspace root (default `W:/Workspace/Java/Simplified`)
