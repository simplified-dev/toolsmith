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
- `toolsmith.jitpack` - jitpack_status / jitpack_build / jitpack_pins / jitpack_set / jitpack_order. Only the versionless `/api/builds/<group>/<artifact>` list is ever read (the per-version endpoint silently STARTS a build); a build is triggered and waited on by a single blocking GET of the `.pom`, no poll loop. Refs are validated from local git first, and a version is charset-checked before it is spliced into a URL path. A list record is never a verdict on its own - `build` goes green only on an HTTP 200 from the `.pom`, since the list reports `ok` for artifacts that 404. `exit_code(result)` maps a result onto 0/1/2 - use it instead of `if r.get("error")`, because a failed build carries an error yet is an ordinary exit-1 verdict.
  - **Reading vs writing a pin.** `_scan_pins` is the one scanner both halves share, so `set` cannot know a dialect `pins` does not. It records each pin's exact character **span**, decoded with `newline=""`, and a rewrite is an offset splice - the artifact id is compared with `==` and never reaches a pattern, so an id holding a regex metacharacter cannot widen the match, and CRLF survives. `set` matches the id EXACTLY where `pins` filters by substring: a filter that over-matches shows extra rows, a rewrite that over-matches edits the wrong artifact. Zero matches is a precondition ERROR carrying the ids that were found - a silent no-op is the failure mode this replaced. The file is re-read strictly before writing and each span re-checked against the pin the scan saw, so a file that moved is refused rather than corrupted. What gets written is the sha **verification resolved**, not the caller's spelling, since `_resolve_ref` answers about the 7-char form of whatever it is handed.
  - **`order` may not claim `strictly` is transitive.** Published gradle module metadata for these artifacts records `{"requires": "<sha>"}`, so an inherited pin is soft and a consumer's own `strictly()` overrides it. Hence `direct` (the module declares the changed artifact, so a stale pin keeps it on the old code) vs `cascade` (convention only). Only FIXED pins carry a graph edge - a `-SNAPSHOT` consumer needs no edit, so it earns no commit, so it does not propagate.

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
- `jitpack_set(artifact, sha, modules?[], check=False, no_verify=False, include_snapshots=False)` -> rewrites the pin; status is one of written, checked, unchanged, unbuilt, unverified, write-failed, precondition. Destructive when it returns "written"
- `jitpack_order(artifact)` -> the re-pin cascade; offline, no network
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
