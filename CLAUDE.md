# toolsmith

General-purpose Java workspace dev toolkit for AI agents, packaged as a Claude Code plugin. Discovers modules in any Java workspace and exposes deterministic tools (gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize, module lookup) as an MCP server + a `toolsmith` CLI, plus bundled Java refactor/move/audit skills. Python / FastMCP. Nothing hardcoded to a checkout - `toolsmith setup` discovers and caches the module map.

## Module Structure
- `toolsmith.cli` - the `toolsmith` command: setup, serve, `java {locate,reorder,docs}`, `gradle {modules,verify,tally}`, `jitpack {status,build,pins,set,order}`, `branch {finish}`
- `toolsmith.server` - FastMCP stdio server; tools forward to the modules below
- `toolsmith.discovery` - scan a root for gradle modules, assign shorthands, write/read the cache, resolve the active root
- `toolsmith.modules` - cache-backed resolve_module / package_root / find_gradle_root / get_modules
- `toolsmith.gradle` - gradle_verify (reliable exit code, noise/signal filter)
- `toolsmith.tally` - gradle_tally (JUnit XML)
- `toolsmith.imports` - java_reorder_imports (IntelliJ Default layout)
- `toolsmith.javadoc` - the bundled javadoc auditor/normalizer
- `toolsmith.jitpack` - jitpack_status / jitpack_build / jitpack_pins / jitpack_set / jitpack_order. Only the versionless `/api/builds/<group>/<artifact>` list is ever read (the per-version endpoint silently STARTS a build); a build is triggered and waited on by a single blocking GET of the `.pom`, no poll loop. Refs are validated from local git first, and a version is charset-checked before it is spliced into a URL path. A list record is never a verdict on its own - `build` goes green only on an HTTP 200 from the `.pom`, since the list reports `ok` for artifacts that 404. `exit_code(result)` maps a result onto 0/1/2 - use it instead of `if r.get("error")`, because a failed build carries an error yet is an ordinary exit-1 verdict.
  - **Reading vs writing a pin.** `_scan_pins` is the one scanner both halves share, so `set` cannot know a dialect `pins` does not. It records each pin's exact character **span**, decoded with `newline=""`, and a rewrite is an offset splice - the artifact id is compared with `==` and never reaches a pattern, so an id holding a regex metacharacter cannot widen the match, and CRLF survives. `set` matches the id EXACTLY where `pins` filters by substring: a filter that over-matches shows extra rows, a rewrite that over-matches edits the wrong artifact. Zero matches is a precondition ERROR carrying the ids that were found - a silent no-op is the failure mode this replaced. The file is re-read strictly before writing and each span re-checked against the pin the scan saw, so a file that moved is refused rather than corrupted. What gets written is the sha **verification resolved**, not the caller's spelling, since `_resolve_ref` answers about the 7-char form of whatever it is handed.
  - **`order` may not claim `strictly` is transitive.** Published gradle module metadata for these artifacts records `{"requires": "<sha>"}`, so an inherited pin is soft and a consumer's own `strictly()` overrides it. Hence `direct` (the module declares the changed artifact, so a stale pin keeps it on the old code) vs `cascade` (convention only). Only FIXED pins carry a graph edge - a `-SNAPSHOT` consumer needs no edit, so it earns no commit, so it does not propagate.
- `toolsmith.branch` - branch_finish: the end-of-branch ritual (push -> body file -> `gh pr create` -> `gh pr merge --merge` -> checkout -> pull -> validate -> `git branch -d`). Every precondition is established before the first push, so a refusal changes nothing, and each step reads its own already-done state so a re-run resumes rather than repeats. `exit_code(result)` maps a result onto 0/1/2 - a declined confirmation and a failed step are both ordinary exit-1 verdicts, where a precondition is 2.
  - **The post-merge check is ANCESTRY, never sha equality.** A true merge leaves a MERGE COMMIT at the base tip, so `rev-parse <base> == rev-parse <branch>` is false on every successful merge; what holds is `git merge-base --is-ancestor <branch-sha> <base>`. The branch tip is captured BEFORE the checkout, since the branch is about to be deleted. The delete is `git branch -d` and never `-D`, because `-d` refuses a branch the base does not contain and is the backstop if that check is ever wrong.
  - **The merge method is `--merge` and a squash or rebase is refused**, not honoured: commits here are often independently gated units, and flattening them destroys the per-commit revert granularity that gating produced. `--squash` and `--rebase` are declared as CLI flags only so asking for one gets the reason instead of an argparse usage error.
  - **The base branch is detected**, from `refs/remotes/origin/HEAD` then `gh repo view`, never hardcoded - this workspace uses `master` and other repos do not. The PR body goes to a FILE (`--body-file`), composed from the branch's commit subjects when none is given, because bodies carry backticks, `$` and apostrophes and a heredoc parse-errors on the long ones.
  - **A merge needs a confirmation.** With a TTY it prompts; without one it refuses before mutating anything unless `--yes` is passed, so an unattended run cannot merge by accident. `--no-merge` stops after the pull request, and needs no confirmation because no merge is planned.
  - `gh` is reached through one injectable seam (`_gh`, or the `gh` argument), which is what makes the suite offline; the git half of the tests runs against real temp repositories, since the ancestry rule is a fact about git that a fake could be made to agree with a wrong version of.
  - **`branch finish` is CLI-only**, for a harder reason than `jitpack pins` being a wide table: it pushes, opens and merges, so it stays a thing a human invokes rather than a tool an agent can reach for on its own initiative. The project owner's standing rule is that the user decides when to push. Do not register it in `toolsmith.server`.

## The naming rule
**A prefix marks WHO CAN USE a command, not what it happens to parse.** A gradle project that
carries no Java source cannot use `java docs` at all, and a Java tree with no gradle build cannot use
`gradle verify`; a JMH gate reads gradle's stdout yet only a Java project has benchmarks to gate, so
it is `java-jmh-regression-gate`. Four groups: `java` (needs Java source), `gradle` (needs a gradle
build), `jitpack` (an external service) and `branch` (git). `setup` and `serve` stay top-level -
they bootstrap the rest and belong to no subject. The same prefixes name the MCP tools and the
skills, so put a new one under the umbrella naming what it needs to exist.

Discovery is the other half: a skill's frontmatter `description` and an MCP tool's docstring are what
get matched when one is chosen, so each states its requirement in words - a `java-*` skill says Java
source, `gradle-verify-gate` says Gradle, a `gradle_*` tool says it needs a gradle module.

## Entry Points
- `toolsmith <subcommand>` (console script) / `python -m toolsmith <subcommand>`
- Four nested subcommand groups: `toolsmith java {locate,reorder,docs}`, `toolsmith gradle
  {modules,verify,tally}`, `toolsmith jitpack {status,build,pins,set,order}` and `toolsmith branch
  {finish}`
- Each grouped subcommand keeps its former top-level spelling as a deprecated alias: same handler,
  a one-line notice on stderr naming the current spelling, and no `--help` entry (argparse renders a
  SUPPRESS help as the literal `==SUPPRESS==` for a subparser, so the alias is registered with no
  help at all, and the top-level `metavar` keeps it out of the usage line). One `_GROUPED_COMMANDS`
  row declares both spellings, and the argument shape is a function so neither copy can drift.
- Plugin MCP: `.mcp.json` launches `toolsmith serve`
- Bundled skills under `skills/` (auto-discovered when the plugin is enabled)

## MCP tools
- `gradle_modules()` -> discovered inventory (name, path, package, shorthand, buildable)
- `gradle_verify(module, tasks?, tail=25, compile_only=False)`
- `gradle_tally(module, subdir="", fails=15)`
- `java_reorder_imports(paths[], check=False)`
- `java_docs_normalize(paths[], fix=False, scope="all", prefix?[])`
- `jitpack_status(module, refs?[])` -> per-ref built/absent/error state; read-only, never triggers
- `jitpack_build(module, ref="", timeout=480.0, force=False)` -> one build; status is one of built, already-built, failed, cached-failure, timeout, in-flight, symbolic, precondition, error. `timeout` is INCONCLUSIVE (re-running attaches to the same build); `cached-failure` needs a new commit, not a retry
- `jitpack_set(artifact, sha, modules?[], check=False, no_verify=False, include_snapshots=False)` -> rewrites the pin; status is one of written, checked, unchanged, unbuilt, unverified, write-failed, precondition. Destructive when it returns "written"
- `jitpack_order(artifact)` -> the re-pin cascade; offline, no network
- `jitpack pins` is CLI-only (a wide human table, like setup and `java locate`), not an MCP tool
- `branch finish` is CLI-only too, and for a harder reason: it pushes, opens a pull request and merges it, so it stays a thing a human invokes rather than a tool an agent can reach for on its own initiative

## Discovery / cache
- `toolsmith setup [ROOT]` writes `<root>/.toolsmith/modules.json` + registers the root in `~/.config/toolsmith/roots.json`. Optional `<root>/.toolsmith/aliases.json` overrides shorthands.
- Root resolution order: explicit arg -> `TOOLSMITH_ROOT` env -> cwd walk-up for a `.toolsmith` cache -> registry default.
- Base package = the single-child dir chain under `src/main/java`. Traps (dir name != package): `collections`->`dev.simplified.collection`, `utils`->`dev.simplified.util`, `gson-extras`->`dev.simplified.gson`, `spring-framework`->`dev.simplified.serverapi`. `gradle_modules` / `toolsmith gradle modules` return the truth - do not guess.

## Import order (java_reorder_imports)
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
