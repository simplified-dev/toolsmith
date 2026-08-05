# toolsmith

General-purpose Java workspace dev toolkit for AI agents, packaged as a Claude Code plugin. Discovers modules in any Java workspace and exposes deterministic tools (gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize, module lookup) as an MCP server + a `toolsmith` CLI, plus bundled Java refactor/move/audit skills. Python / FastMCP. Nothing hardcoded to a checkout - `toolsmith setup` discovers and caches the module map.

## Module Structure
- `toolsmith.cli` - the `toolsmith` command: subcommands (setup, serve, modules, verify, tally, reorder, javadoc, locate, jitpack)
- `toolsmith.server` - FastMCP stdio server; tools forward to the modules below
- `toolsmith.discovery` - scan a root for gradle modules, assign shorthands, write/read the cache, resolve the active root
- `toolsmith.modules` - cache-backed resolve_module / package_root / find_gradle_root / get_modules
- `toolsmith.gradle` - gradle_verify (reliable exit code, noise/signal filter)
- `toolsmith.tally` - test_tally (JUnit XML)
- `toolsmith.imports` - reorder_imports (IntelliJ Default layout)
- `toolsmith.javadoc` - the bundled javadoc auditor/normalizer
- `toolsmith.jitpack` - jitpack_status / jitpack_build / jitpack_pins. Only the versionless `/api/builds/<group>/<artifact>` list is ever read (the per-version endpoint silently STARTS a build); a build is triggered and waited on by a single blocking GET of the `.pom`, no poll loop. Refs are validated from local git first, and a version is charset-checked before it is spliced into a URL path. A list record is never a verdict on its own - `build` goes green only on an HTTP 200 from the `.pom`, since the list reports `ok` for artifacts that 404. `exit_code(result)` maps a result onto 0/1/2 - use it instead of `if r.get("error")`, because a failed build carries an error yet is an ordinary exit-1 verdict.

## Entry Points
- `toolsmith <subcommand>` (console script) / `python -m toolsmith <subcommand>`
- Plugin MCP: `.mcp.json` launches `toolsmith serve`
- Bundled skills under `skills/` (auto-discovered when the plugin is enabled)

## MCP tools
- `list_modules()` -> discovered inventory (name, path, package, shorthand, buildable)
- `gradle_verify(module, tasks?, tail=25, compile_only=False)`
- `test_tally(module, subdir="", fails=15)`
- `reorder_imports(paths[], check=False)`
- `javadoc_normalize(paths[], fix=False, scope="all", prefix?[])`
- `jitpack_status(module, refs?[])` -> per-ref built/absent/error state; read-only, never triggers
- `jitpack_build(module, ref="", timeout=480.0, force=False)` -> one build; status is one of built, already-built, failed, cached-failure, timeout, in-flight, symbolic, precondition, error. `timeout` is INCONCLUSIVE (re-running attaches to the same build); `cached-failure` needs a new commit, not a retry
- `jitpack pins` is CLI-only (a wide human table, like setup and locate), not an MCP tool

## Discovery / cache
- `toolsmith setup [ROOT]` writes `<root>/.toolsmith/modules.json` + registers the root in `~/.config/toolsmith/roots.json`. Optional `<root>/.toolsmith/aliases.json` overrides shorthands.
- Root resolution order: explicit arg -> `TOOLSMITH_ROOT` env -> cwd walk-up for a `.toolsmith` cache -> registry default.
- Base package = the single-child dir chain under `src/main/java`. Traps (dir name != package): `collections`->`dev.simplified.collection`, `utils`->`dev.simplified.util`, `gson-extras`->`dev.simplified.gson`, `spring-framework`->`dev.simplified.serverapi`. `list_modules` / `toolsmith modules` return the truth - do not guess.

## Import order (reorder_imports)
IntelliJ Default: group1 other (ASCII), blank, group2 `javax.*` then `java.*`, blank, group3 static. Flat-ASCII sort; only `java`/`javax` special-cased; wildcards + CRLF/LF preserved; idempotent. See `notes/drafts/DRAFT-import-order.md` for the empirical derivation.

## Build / Test
```bash
pip install -e ".[dev]"
python -m pytest -q
```

## Conventions
- Python 3.10+ (workspace runs 3.14). Type hints on public functions; Google-style docstrings. PEP 8.
- Java-source conventions (javadoc/exceptions/etc. in the user global CLAUDE.md) govern the Java files this tool EDITS, not this Python source.

## Info
- Package `toolsmith` 0.1.0, Apache-2.0. Deps: `fastmcp>=1.0` (+ `pytest` for dev).
- `notes/` holds the token-optimization audit that produced this project (raw evidence in `notes/data/` is gitignored).
