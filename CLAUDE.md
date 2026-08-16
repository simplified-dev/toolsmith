# toolsmith

General-purpose Java workspace dev toolkit for AI agents, packaged as a Claude Code plugin. Discovers modules in any Java workspace and exposes deterministic tools (gradle verify, JUnit tally, IntelliJ-faithful import reorder, javadoc normalize, module lookup) as an MCP server + a `toolsmith` CLI, plus bundled Java refactor/move/audit skills. Python / FastMCP. Nothing hardcoded to a checkout - `toolsmith setup` discovers and caches the module map.

## Module Structure
- `toolsmith.cli` - the `toolsmith` command: setup, serve, `java {locate,reorder,docs,json_diff}`, `gradle {modules,verify,tally}`, `jitpack {status,build,pins,set,order}`, `branch {finish}`
- `toolsmith.server` - FastMCP stdio server; tools forward to the modules below
- `toolsmith.discovery` - scan a root for gradle modules, assign shorthands, write/read the cache, resolve the active root
- `toolsmith.modules` - cache-backed resolve_module / package_root / find_gradle_root / get_modules
- `toolsmith.gradle` - gradle_verify (reliable exit code, noise/signal filter)
- `toolsmith.tally` - gradle_tally (JUnit XML)
- `toolsmith.imports` - java_reorder_imports (IntelliJ Default layout)
- `toolsmith.javadoc` - the bundled javadoc auditor/normalizer
- `toolsmith.json_diff` - java_json_diff: walks a JSON capture and a Java class graph in parallel and reports the JSON keys no field binds. What it understands of the Java is a VOCABULARY rather than a language, because the source is read with line regexes; which annotation is honoured, which is inert and which is deliberately unsupported is the table under [JSON key coverage](#json-key-coverage-java_json_diff), and so is the phantom direction. `exit_code(result)` maps a result onto 0/1/2 and the split is the point: unmapped keys are a red gate at 1, where an unreadable capture, a source root that parsed no classes, an unknown root class or a path expression that names nothing never ran the audit at all and are 2 - reporting those as "the classes cover nothing" is the worst available reading of a gate. Every key list is capped at `cap` rows while the count beside it is whole, so `truncated` is what says a list is short.
  - **The report body is an interface, so an addition to it goes on stderr.** The `human` format's stdout is verified byte for byte against `Simplified-Api/hypixel`'s captured profiles response, over four invocations - the union walk, one `--section`, one `--node`, and `--show-mapped --show-unresolved` at 657 KB - so the shape mismatches, the three diagnostics and the truncation warning print on stderr rather than in the body, and `human` is the one format that ends without a verdict line. `gate` prints nothing at all on a pass and one stderr line on a red run, with stdout empty either way, so a job piping stdout to an artifact gets an empty file.
  - **Rendering lives beside the walk rather than in the CLI adapter**, which is the one place this repo departs from `jitpack.py`'s rule that human formatting belongs to the adapter. Every renderer takes the result dict and reads nothing else - no source file, no capture, no environment - so a result carried across a process boundary still renders, and the MCP caller that returns the dict raw is not paying for text it does not print.
  - **`--open` is CLI-only, and the guard is parameter absence.** It writes both projections to files under `%TEMP%/toolsmith-json_diff/` and hands them to IntelliJ's diff viewer (`TOOLSMITH_IDEA` -> `<root>/.toolsmith/editor.json` -> PATH -> per-platform globs, and a failure names the env var and what it searched); `CI` or `TOOLSMITH_NO_LAUNCH` writes the files and refuses to launch. No `java_json_diff` argument selects a launch and `json_diff()` never calls `open_diff` - an import-graph check would prove nothing, since `server.py` imports the module.
- `toolsmith.jitpack` - jitpack_status / jitpack_build / jitpack_pins / jitpack_set / jitpack_order. Only the versionless `/api/builds/<group>/<artifact>` list is ever read (the per-version endpoint silently STARTS a build); a build is triggered and waited on by a single blocking GET of the `.pom`, no poll loop. Refs are validated from local git first, and a version is charset-checked before it is spliced into a URL path. A list record is never a verdict on its own - `build` goes green only on an HTTP 200 from the `.pom`, since the list reports `ok` for artifacts that 404. `exit_code(result)` maps a result onto 0/1/2 - use it instead of `if r.get("error")`, because a failed build carries an error yet is an ordinary exit-1 verdict.
  - **Reading vs writing a pin.** `_scan_pins` is the one scanner both halves share, so `set` cannot know a dialect `pins` does not. It records each pin's exact character **span**, decoded with `newline=""`, and a rewrite is an offset splice - the artifact id is compared with `==` and never reaches a pattern, so an id holding a regex metacharacter cannot widen the match, and CRLF survives. `set` matches the id EXACTLY where `pins` filters by substring: a filter that over-matches shows extra rows, a rewrite that over-matches edits the wrong artifact. Zero matches is a precondition ERROR carrying the ids that were found - a silent no-op is the failure mode this replaced. The file is re-read strictly before writing and each span re-checked against the pin the scan saw, so a file that moved is refused rather than corrupted. What gets written is the sha **verification resolved**, not the caller's spelling, since `_resolve_ref` answers about the 7-char form of whatever it is handed.
  - **`order` may not claim `strictly` is transitive.** Published gradle module metadata for these artifacts records `{"requires": "<sha>"}`, so an inherited pin is soft and a consumer's own `strictly()` overrides it. Hence `direct` (the module declares the changed artifact, so a stale pin keeps it on the old code) vs `cascade` (convention only). Only FIXED pins carry a graph edge - a `-SNAPSHOT` consumer needs no edit, so it earns no commit, so it does not propagate.
- `toolsmith.branch` - branch_finish: the end-of-branch ritual (push -> body file -> `gh pr create` -> `gh pr merge --merge` -> checkout -> pull -> validate -> `git branch -d`). Every precondition is established before the first push, so a refusal changes nothing, and each step reads its own already-done state so a re-run resumes rather than repeats. `exit_code(result)` maps a result onto 0/1/2 - a declined confirmation and a failed step are both ordinary exit-1 verdicts, where a precondition is 2.
  - **It reports where the base ended up** (`base_sha`, read with `rev-parse` once the pull has brought the merge down, and shown as `master@<sha>`). That is what a revert of the landing starts from, and the merge step cannot supply it - at that point the merge exists only on the remote and gh has answered with a pull request number. It is deliberately called the base TIP and not "the merge commit", which it is only while nothing else lands between the merge and the pull. `None` on a dry run and on any failure before the pull.
  - **The post-merge check is ANCESTRY, never sha equality.** A true merge leaves a MERGE COMMIT at the base tip, so `rev-parse <base> == rev-parse <branch>` is false on every successful merge; what holds is `git merge-base --is-ancestor <branch-sha> <base>`. The branch tip is captured BEFORE the checkout, since the branch is about to be deleted. The delete is `git branch -d` and never `-D`, because `-d` refuses a branch the base does not contain and is the backstop if that check is ever wrong.
  - **The merge method is `--merge` and a squash or rebase is refused**, not honoured: commits here are often independently gated units, and flattening them destroys the per-commit revert granularity that gating produced. `--squash` and `--rebase` are declared as CLI flags only so asking for one gets the reason instead of an argparse usage error.
  - **The base branch is detected**, from `refs/remotes/origin/HEAD` then `gh repo view`, never hardcoded - this workspace uses `master` and other repos do not. The PR body goes to a FILE (`--body-file`), composed from the branch's commit subjects when none is given, because bodies carry backticks, `$` and apostrophes and a heredoc parse-errors on the long ones.
  - **A merge needs a confirmation.** With a TTY it prompts; without one it refuses before mutating anything unless `--yes` is passed, so an unattended run cannot merge by accident. `--no-merge` stops after the pull request, and needs no confirmation because no merge is planned.
  - **The repository is resolved in the CLI adapter, not in `toolsmith.branch`.** `branch_finish` takes `repo` as any path inside the repository and walks up to the `.git`, so a module subdirectory resolves to its repo root; `_branch_finish` is what turns a shorthand or module name into that path, through `modules.resolve_module`. Keeping the lookup out of `toolsmith.branch` is what keeps it free of a discovery dependency. A token that resolves to neither a known module nor a directory is a precondition failure: `resolve_module` answers `None` for both "no such module" and "no such directory", and passing that `None` through would read as the current directory and finish whatever branch the shell was sitting on. Discovery scans for GRADLE modules, so a repository that is not one - toolsmith itself - is named by path or by cwd, never by alias.
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
- Four nested subcommand groups: `toolsmith java {locate,reorder,docs,json_diff}`, `toolsmith gradle
  {modules,verify,tally}`, `toolsmith jitpack {status,build,pins,set,order}` and `toolsmith branch
  {finish}`
- A grouped subcommand that MOVED keeps its former top-level spelling as a deprecated alias: same
  handler, a one-line notice on stderr naming the current spelling, and no `--help` entry (argparse
  renders a SUPPRESS help as the literal `==SUPPRESS==` for a subparser, so the alias is registered
  with no help at all, and the top-level `metavar` keeps it out of the usage line). One
  `_GROUPED_COMMANDS` row declares both spellings, and the argument shape is a function so neither
  copy can drift.
- **A subcommand that never had a top-level spelling declares `old = None` in that same table and
  gets no alias** - there is nothing to deprecate, and `toolsmith json_diff` is an argparse usage
  error rather than a second name. It stays in the table so the group's help text, argument shape
  and handler keep one declaration; a separate registration block would duplicate the wiring the
  table exists to centralise. The next new command meets the same slot.
- Plugin MCP: `.mcp.json` launches `toolsmith serve`
- Bundled skills under `skills/` (auto-discovered when the plugin is enabled)

## MCP tools
- `gradle_modules()` -> discovered inventory (name, path, package, shorthand, buildable)
- `gradle_verify(module, tasks?, tail=25, compile_only=False)`
- `gradle_tally(module, subdir="", fails=15)`
- `java_reorder_imports(paths[], check=False)`
- `java_docs_normalize(paths[], fix=False, scope="all", prefix?[])`
- `java_json_diff(json_path, src, root, node="", union="", section?, show_mapped=False, show_unresolved=False, max_depth=12, cap=200, opaque?[], map_types?[], seq_types?[], wrapper_types?[], strict?[], phantom=False, fail_on_phantom=False)` -> the walk's dict, unrendered. The first parameter is `json_path` and not `json` because pydantic warns that a field named `json` shadows an attribute of the argument model base, which is fatal under `-W error`; the CLI flag stays `--json` and the result key stays `json`. There is no `open` argument - the editor hand-off is CLI-only
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
- **`kind` and `repo` are two independent axes, and a command reads whichever one it divides on.** `kind` is the build system, from one marker file each (`build.gradle`/`build.gradle.kts`, `pom.xml`, `pyproject.toml`/`setup.py`/`setup.cfg`), first match winning so a gradle project holding a `pyproject.toml` for its dev scripts stays gradle. `repo` is whether the directory is a git repository root. They differ in BOTH directions: a gradle module can sit inside a repo it does not own, and a repo can carry no build file at all - a directory earns a row by either.
  - **A non-gradle kind is refused BEFORE `find_gradle_root`, because that walk goes UP.** There is a `gradlew` at `Simplified-Dev/` and at the workspace root, so a python project under them resolves a SIBLING project's wrapper and runs a different build rather than failing. `gradle verify` and `gradle tally` both guard on it.
  - **An unrecorded kind is not a wrong kind.** `modules.kind_of` answers `None` both for a token the inventory never heard of and for a bare filesystem path, which `resolve_module` accepts - so a guard refuses a KNOWN wrong kind and lets `None` through, or naming a module by path stops working.
  - **`buildable` means a JVM source tree**, not merely a `src/` directory: a python project has one of those too, and `java locate` would otherwise search it for `.java`.
  - The cache carries no compatibility shim. A `modules.json` predating these fields raises rather than defaulting - re-run `toolsmith setup`.

## Import order (java_reorder_imports)
IntelliJ Default: group1 other (ASCII), blank, group2 `javax.*` then `java.*`, blank, group3 static. Flat-ASCII sort; only `java`/`javax` special-cased; wildcards + CRLF/LF preserved; idempotent.

## JSON key coverage (java_json_diff)

It diffs a JSON document against a Java source tree: one side is a captured response, the other is
source, and the answer is which JSON keys no Java field binds. It compares no two DTOs and reads no
schema, so it is a key-coverage audit rather than a DTO differ - the wire is the authority and the
classes are what is checked against it. `--phantom` asks the same pair the opposite question and is
described below.

It sits under `java` because the prefix names WHO CAN USE a command: it needs Java source and a JSON
file and nothing else, where `gradle verify` needs a build. A Java tree with no gradle build can run
it, and a gradle project carrying no Java source cannot.

**The annotation vocabulary.** Every `dev.simplified.gson` annotation is one of three things here, and
a reader must never have to re-derive which. An `unsupported` row names the wrong answer it produces,
so a surprising result is recognisable rather than mysterious.

| Annotation | Status | Effect on key coverage | Why, and what it costs |
|---|---|---|---|
| `@SerializedName("x")` | honoured | replaces the field's Java name as the single wire key it consumes | gson's own rename: without it the walk looks for a camelCase key the wire never sends |
| `@SerializedName(value = , alternate = )` | honoured under `strict=names` | the value plus every alternate binds the same field | off, only the lone-literal form is read and a named-parameter site falls back to the Java name. **False positive**: on a Hypixel profile that sends them, 28 `community_upgrades.upgrade_states[].started_by` keys report unmapped against a field that binds them |
| `@SerializedPath` | honoured | the field consumes a dotted CHAIN, and fields sharing a prefix merge into one trie level | the merge is the factory's find-or-create: three fields share `profile.` on one Hypixel member, and two objects where there should be one means the last write wins. Under `strict=names` the path wins over a co-located `@SerializedName`, which names only the flattened key the delegate sees; off, whichever annotation is written first wins, and no field in the workspace carries both |
| `@Extract` | honoured under `strict=extract` | binds no wire key at all - it is fed from another field's overflow | off, the value is read as a wire path, which invents a binding wherever the source field's wire name differs from its Java name (`treeGifts` for `tree_gifts`). The invention matches nothing, so it costs no unmapped row; it costs 21 of the 132 phantoms on the Hypixel capture |
| `@Capture` | honoured | a catch-all capture consumes every sibling no declared field and no filtered capture claimed, so a class carrying one can never report an unmapped key at its own level | the factory classifies each entry in three ordered passes, and its known-key discovery skips capture fields, so a non-descend capture's own `@SerializedName` binds on write alone |
| `@Capture(descend = )` | honoured under `strict=capture` | consumes the field's own key, then applies the capture rules to the keys INSIDE that object rather than to its siblings | off, descent is inferred from a capture carrying an explicit name. That fires on exactly the sites that set the flag today, and on a named capture that does not set it the inference binds a key the factory never binds |
| `@Capture(filter = )` | honoured | narrows the field to sibling keys the regex matches | `re.search` is `Matcher.find()`, which is what the factory calls. Under `strict=capture` an inner key the descend filter rejects is reported, because the factory drops it outright rather than overflowing it |
| `@Capture` grouping affixes | **deliberately unsupported** | every filter-matched key reads mapped | **False negative**: in grouping mode the factory strips the match and folds the remainder by affix, silently DISCARDING a stripped key that matches no prefix, no suffix and no bare field. Modelling it means porting the affix derivation of the whole factory; it changes no answer on any capture in this workspace, and the defect it would catch is a wire key the DTO half-models rather than one it misses entirely |
| `@Collapse` | honoured under `strict=collapse` | an object-shaped wire value reaches a sequence field only through it: the keys are entry ids and each value is one element | off, ANY sequence field walks an object's values as elements. **False negative**: a sequence field over a JSON object without `@Collapse` throws `Expected BEGIN_ARRAY but was BEGIN_OBJECT` at decode and reads fully mapped here, which hides the worst class of bug behind a green gate. On, it is a `shape_mismatches` row and the only finding that names no key - it is why `ok` is more than `unmapped == 0`. A map-typed `@Collapse` needs no branch: the map already walks each value as V |
| `@Key` | honoured | removes the field from the binding table entirely | it is injected from the enclosing `@Collapse` entry's key and is never a key inside the value object, so leaving it in binds a key the object never carries. The pattern must not also match `@KeyField` |
| `@Flatten` | honoured under `strict=flatten` | consumes one wrapper level, named by `value()`, inside each map or collection entry before the declared element type applies | off, the element type is walked against the wrapper: the wrapper's member name over-reports once per entry and the real keys inside it are never checked. It bites only on a class-typed element - a `ConcurrentMap<String, Integer>` stops one level above the wrapper and reads clean either way |
| `@Lenient` | inert | none: the field claims every key in its subtree whichever way an entry goes | a filtered entry is held in Overflow and merged back on write, so nothing is lost and reporting one unmapped is a false positive. A third bucket needs the runtime type-compatibility check re-implemented and is undecidable exactly where it matters, on declared types the walk cannot resolve. **Sharp edge**: for a class-valued `@Lenient` map, an entry that really went to overflow has its inner keys reported unmapped |
| `@Split` | inert | none: it consumes the key it would consume anyway and splits that key's string VALUE | a string has no child keys, so the walk never reaches anything the annotation moved |
| `@Fallback` | inert | none: it changes which enum constant an unrecognised NAME resolves to | it marks an enum CONSTANT, and the walk returns on an enum without reading its body |

**The strictness switches all default off**, and each is named for the annotation whose handling it
corrects: `names`, `extract`, `capture`, `collapse`, `flatten`, or `all`. Every one of them is a fix,
and a fix that can change a verdict has to be asked for - a report a caller has calibrated against
does not move underneath them. The cost is measurable rather than theoretical: on
`Simplified-Api/hypixel`'s captured profiles response, `strict=capture` alone moves the mapped list
from 6,977 rows to 7,391 while the unmapped count stays at 4, because a matched capture key spends
the collection's own level and the walk stops going one level too deep. That correction is the one
that a pinned mapped report would have to be re-cut for, which is why it is a switch and not the
default.

**The phantom direction exists, behind a flag, and does not gate.** `phantom` projects both sides to
path lines - `.key`, `[]` for an array, `{}` for any object value, cut at `max_depth` on both sides
by the same rule - and reports the paths the class graph can bind that the document never fed. `ok`
never moves for one, and `exit_code` answers 1 only under `fail_on_phantom`. The gate's verdict is
about keys the wire sends that nothing binds; a field nothing feeds is the opposite question, and
turning a new failure class on silently re-verdicts every caller that calibrated against the first
one. `--section` together with a projection is refused as a precondition, because a section narrows
the capture and not the class graph, so every field outside it would read as a phantom of the
narrowing.

**What it found, said plainly: mostly noise.** On the Hypixel member walk (937 wire paths against
1,053 bindable) there are 132 phantoms. 56 sit under a subtree the account never sent and 42 under a
container the wire sent empty, so 98 of them - 74% - are structure rather than findings; a missing
subtree there is a player's privacy setting. 21 are artefacts of this tool's own default annotation
handling and disappear under `strict=all`, which adds none of its own. That leaves roughly 26 worth
reading: optional wire keys this account lacks (`rift.access.pass`, `item_data.favorite_arrow`) and
genuine shape questions. On a single sparse profile it is 952 phantoms of which 742 sit under an
absent subtree, which is useless as it stands. The judgement is that the direction pays off on a
unioned capture and after reading the live-parent slice, and nowhere else - a per-section "the wire
sent nothing here at all" fold is what would make the rest readable, and the parent-presence
classification already computes it.

The projection's other half repaid itself independently. The 16 wire-only lines on that run are the
4 unmapped keys plus 2 children plus **10 keys the walk is structurally blind to** under the default
switches: a `@Flatten` wrapper's contents, and `@Lenient` overflow under a `ConcurrentMap<String,
Integer>`. Those appear in no other report.

**Five formats, detected, and every one of them spellable.** `agent`, `human`, `gate`, `diff`,
`json`. Detection is a pure function of the environment and one boolean: `CI` or `GITHUB_ACTIONS`
set to anything meaning yes wins first and takes `gate`, then `CLAUDECODE` takes `agent`, then a
terminal takes `human`, and anything else - a pipe, a file, a cron job - takes `agent`, the one
format that is bounded, plain and complete on one stream. A CI-shaped variable holding `""`, `0` or
`false` in any case counts as unset, because the convention every Node tool follows honours an
exported `CI=false` and several hosts tell their users to export exactly that. `TERM` is never read:
an agent harness sets it while stdout is not a terminal, so it says nothing about who is reading.

**`--format` overrides the detection, always, and `gate` is one of the names it accepts.** A default
that flips silently under a pipe makes a CI failure impossible to reproduce by hand, so reproducing
a red CI run means asking for the format CI used, and that format needs a name a human can type.
Colour follows the published `NO_COLOR` rule - present AND non-empty disables it - so `NO_COLOR=` is
how a caller un-sets one it inherited; reading presence alone would make that spelling the one thing
it cannot express. The `agent` report prints 50 findings and then defers the rest, with the counts
line exact whatever the cap drops, because an agent's next move is to open a file and a list longer
than a screen gets re-summarised rather than read. `diff` raises on a run that did not project
rather than returning a one-sided report, which at a call site is indistinguishable from a working
one and lands in a pipeline as a false clean.

**What it does not do, and the trade each one is.**

- **The Java is read with line regexes**, so a field declaration split across two lines matches
  nothing at all. The trade buys a tool with no parser dependency that runs against source which
  need not compile; what it costs is silence, so the silence is reported: a class or record that
  parsed zero fields is a row in `empty_classes`, which is what an invisible declaration looks like
  from the outside. The row carries `kind` because a record declares its components in the header
  where the field regex never looks, so every record is empty by construction and only a `class` row
  is a question. On the Hypixel tree the 15 rows are 8 classes and 7 records: 11 of them the stat
  layer, which binds nothing, and the rest a gson contributor, a nested adapter, an exception and a
  static helper. None is a DTO the walk descends into. A real parser is what changes this.
- **A simple type name is resolved by a global lookup when no nested or same-file class answers**,
  and a global hit with more than one candidate takes whichever file sorted first. The wrong one
  walks the right JSON against the wrong class and reports its keys unmapped, which reads as a
  finding, so every ambiguous name is recorded in `ambiguous_types` and the resolution order is left
  alone: refusing would fail runs that are fine, and the ledger is what makes a surprising block of
  unmapped keys explicable. It is empty on a single-module source root and fills up on a
  workspace-wide one. Import-aware resolution is what changes this, and it needs the parser above.
- **A `strict=capture` run reports more mapped keys than a default one** (6,977 to 7,391 on the
  Hypixel capture). Off, a class-valued capture's own keys are invisible and the projection half of
  the same result disagrees with the walk. It is a switch because the correction moves a pinned
  report; re-cutting that report is what changes it.
- **Under `strict=flatten` an unmapped row names a path the capture does not contain**: the walk
  consumes the wrapper level and does not put it in the path, where the projection spells the same
  key with it. Grep for the reported path and nothing is found. Having `_unwrap` return the consumed
  member name is what changes it.
- **Under `strict=names` with a projection, an unused `@SerializedName` alternate is a phantom no
  capture can clear** - value and alternates are mutually exclusive, so at most one spelling can ever
  arrive. `--fail-on-phantom` is therefore permanently red for a DTO using alternates. Projecting
  the primary key alone, or registering the alternates as one wildcard group, is what changes it.
- **`--format human` ends without a verdict line**, where every other command here prints one. The
  body is compared whole against the captured reports, so a line appended to stdout is a change to
  an interface; the verdict is available from the exit code and from every other format.
- **Two parameters are reachable from Python only**: `render_diff(other=...)`, which compares two
  runs' document sides and answers what moved between two captures, and `find_editor(refresh=True)`.
  No CLI flag selects either. The editor cache self-heals when the path it recorded is gone, so
  `refresh` earns a flag only when something needs it sooner.

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
