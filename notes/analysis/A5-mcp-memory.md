# A5 - Skills-as-MCP, Memory/Context, Mempalace, Graph-Nav

Dense analysis backing `DRAFT-mcp-memory-recommendations.md`. Four assessments, each
verdict = adopt / trial / skip with a concrete first step. All paths absolute.

## 0. Runtime ground-truth (measured this session, not assumed)

Toolchain (verified via `--version` / PATH):
- Python 3.14.3 (`python` and `python3` both resolve), Node v22.15.0, JDK 25/21/17/8
  (Temurin), IntelliJ IDEA Ultimate + Community installed.
- `python -c "import fastmcp"` -> fastmcp 1.0 present; bare `import mcp` fails. So the
  MCP Python stack is only partially installed; a build would `pip install fastmcp`
  (jlowin 3.x) or use the Node `@modelcontextprotocol/sdk` (node is clean).

Live MCP surface THIS session (from the deferred-tool listing): IntelliJ_IDE (~60
tools), DeepWiki, Gmail, Calendar, Drive, Postman, ide.getDiagnostics. That is it.

What is NOT live despite being "configured" somewhere:
- `code-review-graph` MCP (semantic_search_nodes, callers_of, get_impact_radius, ...)
  is referenced ONLY by three skill files
  (`~/.claude/skills/{debug-issue,explore-codebase,refactor-safely}.md`). It appears
  in NO live tool list and in NO config: `~/.claude/data/mcp-config.json` lists only
  `memory` (docker mcp/memory knowledge-graph), `deepwiki`, `sequentialthinking`,
  `context7`(disabled). Grep for `code-review-graph` across `~/.claude` hits only the
  three skill docs + this session's own subagent transcripts. => It is aspirational /
  not installed, or a project-scoped server that never actually loaded. This single
  fact reshapes recommendation #4.
- `jdtls-lsp@claude-plugins-official` plugin is ENABLED in `settings.json`
  (`enabledPlugins`), but the `jdtls` binary is NOT on PATH (`which jdtls` -> none).
  The plugin ships only LICENSE + README (a launcher stub, no bundled server); the
  README says install jdtls via brew/AUR/manual. So the native Java LSP path is
  wired-but-dormant - a second, unused symbol-nav backend.
- `~/.claude/data/mcp-config.json` is an Agentic-Substrate agent->server MAPPING
  template (version "4.0"), not the live Claude Code MCP registry. Its `memory`
  server (docker `mcp/memory`) is also not in this session's tool list.

The through-line: the user has THREE symbol-navigation backends nominally available
(IntelliJ MCP = live+accurate, jdtls-lsp = dormant, code-review-graph = phantom) and
in practice used none of them - `search_symbol` was called 1 time ever against 3852
greps. The bottleneck is routing/invocation, not backend capability.

## 1. Skills-as-MCP (build a local server?)

### 1a. Classify every candidate skill by what it actually contains

The task frames this as "convert the Java skills into one local MCP server". But the
skills are not homogeneous. Read each one - two categories emerge:

PROMPT-ROUTING skills (no logic of their own; they just tell Claude to call an
existing IntelliJ MCP tool with the right pattern):
- `java-bulk-rename` -> `mcp__IntelliJ_IDE__rename_refactoring` + sed fallback +
  `reformat_file` + gradle-verify. Pure decision tree.
- `java-symbol-search` -> `search_symbol`/`search_regex`/`find_files_by_glob`.
- `java-find-usages` -> `search_symbol` + `get_symbol_info`.
- `java-import-audit`, `java-modifier-audit`, `java-record-audit` -> `get_file_problems`
  + targeted `search_regex`.
- `gradle-verify-gate` -> advises `./gradlew :module:compileJava :module:test`; ships
  NO reusable wrapper.
Wrapping any of these as an MCP tool means writing a server that just forwards to
another MCP (IntelliJ) or shells `./gradlew`. Zero determinism gained, and it moves a
free on-demand skill into an always-listed schema (see 1b). Net negative.

REAL-LOGIC tools (deterministic code that already runs, or is hand-authored over and
over):
- `javadoc-normalize` -> a genuine 23 KB `normalize.py` with a rich CLI
  (`--fix`, `--scope {class|method|field|all}`, `--prefix`), already invoked via Bash.
- `java-exception-class-gen` -> pure template, no MCP needed (Skill emits text).
- The 869 ad-hoc-tool events + the single most-repeated command shape:
  `cd MODULE && ./gradlew compileJava [test] -q 2>&1 | grep -vE "incubating|warning" |
  tail -N ; echo "EXIT: ${PIPESTATUS}"` (hundreds of hand-rewritten variants), plus the
  recurring test-XML tally (grep/awk/python over `build/test-results/test/*.xml`) and
  the `sortimports.py` reorderer Claude re-wrote to `/tmp` repeatedly. THESE are the
  real candidates - deterministic, high-frequency, currently re-authored from scratch.

### 1b. The context-cost asymmetry that decides it

From `build-mcp-server/references/tool-design.md` (authoritative): "Every tool schema
is tokens Claude spends EVERY turn. Thirty tools with rich schemas can eat 3-5k tokens
before the conversation even starts." A skill costs ~1 line in the skill index until
invoked, then loads on demand.

So the trade is exact:
- Skill = cheap baseline, but the model must CHOOSE to invoke (and the histogram proves
  it often does not - see 4c).
- MCP tool = deterministic typed entry that is ALWAYS visible (Claude cannot "forget"
  it), paid for with per-turn schema tokens in every session, Java or not.

Decision rule that falls out:
1. If the skill only routes to an existing MCP -> keep it a skill (adding an MCP proxy
   pays schema cost for zero capability).
2. If the logic is deterministic AND hand-re-authored so often that the always-on
   schema cost is amortized -> MCP tool IS justified, BUT scope it so the schema only
   loads where Java work happens (project `.mcp.json` at the workspace root), not
   globally.
3. If deterministic but invoked rarely / already fine as a Bash-called script -> keep
   it a committed script the skill/CLAUDE.md points at (e.g. `normalize.py` today).
   No MCP.

### 1c. Concrete minimal architecture (if built)

ONE stdio server, project-scoped, ~4-6 tools - only the deterministic-and-frequent
operations, NOT the routing skills:

Server: `simplified-tools` (FastMCP, Python - reuses the existing `normalize.py`).
Registered at `W:/Workspace/Java/Simplified/.mcp.json` (loads ONLY under that tree).

Tools (each `readOnlyHint` except where noted):
- `gradle_verify(module, tasks=["compileJava","test"], tail=40)` -> runs the gradle
  gate, strips `incubating|warning|Deprecated`, returns
  `{exit_code, failed_first, tail}` structured. Kills the #1 hand-rewritten shape.
- `test_tally(module)` -> parses `**/build/test-results/test/*.xml`, returns
  `{total, passed, failed, skipped, failing_tests[]}`. Kills the recurring XML-tally.
- `javadoc_normalize(paths[], fix=false, scope="all", prefix=[])` -> shells the
  existing `normalize.py`; returns its audit JSON. (Not destructive when fix=false;
  `destructiveHint:true` when fix=true.)
- `sort_imports(paths[])` -> the IntelliJ-Default-order import reorderer (the correct
  version of the throwaway `sortimports.py`), returns a diff or applies. Only worth it
  once an IDE-accurate ordering exists; otherwise defer to `reformat_file`.
- (optional) `git_move_java(src, dst)` -> the file-mover: `git mv` + package/dir fixup,
  the git-history-preserving move the CLAUDE.md rule mandates. `destructiveHint:true`.

Everything else STAYS a skill. Read/write split honored (normalize audit vs --fix are
separate arg states but flagged via annotations; gradle_verify is read-only).

### 1d. Effort estimate + verdict

Effort (AI-assisted elapsed, me driving + you): ~2-3 hours to scaffold the FastMCP
server, wire the 3 highest-value tools (gradle_verify, test_tally, javadoc_normalize
which already exists), the `.mcp.json`, and smoke-test via `/mcp`. Human-developer
time unaided: ~1-1.5 days (MCP boilerplate, XML parsing, gradle stdout quirks,
Windows PIPESTATUS semantics). The scaffold is drafted in
`DRAFT-simplified-tools-mcp-server.py`.

VERDICT: TRIAL, narrow. Build the small project-scoped MCP for the 3 deterministic
high-frequency operations ONLY. Do NOT convert the routing skills (bulk-rename,
symbol-search, find-usages, the auditors) - they must stay skills. `normalize.py`
stays a script and is merely EXPOSED through the MCP for typed invocation. If after a
week the gradle_verify/test_tally tools are not actually displacing the hand-authored
shapes, the always-on schema cost is not paying for itself - roll back to skills+scripts.

## 2. Mempalace

### 2a. Fit against the hard requirement

Hard requirement (from context): memory must be AI-auto-invocable, no manual human
step. Mempalace ships a Claude Code plugin with hooks: Stop (save every ~15 msgs),
SessionEnd (final save), PreCompact (save before compaction) + a 36-tool MCP server.
The hooks fire automatically; the MCP recall tools are model-invocable. So on the
"auto-invocable, no manual step" axis it PASSES cleanly - this is exactly what the
requirement asks for and what file-memory (which the user hand-edits) does not provide.

### 2b. Value vs the existing file-memory (add or duplicate?)

The two are different layers, not duplicates:
- File-memory (`MEMORY.md` index + 5 dedicated files, 146 KB architecture doc) is
  CURATED, SEMANTIC, LOW-VOLUME: locked decisions, "do NOT rename X", the Hibernate
  property gotchas. It is authoritative because a human vetted it.
- Mempalace is EPISODIC, HIGH-VOLUME, VERBATIM: it would index the ~410 MB of past
  session transcripts and recall "how did I fix the jitpack poll", "what exact grep
  filtered the gradle noise", "which file held the TypeRegistrar postProcess". That
  long tail is exactly what is being RE-DERIVED today (869 ad-hoc-tool events, the
  3852 greps rediscovering the same files).

So it adds a genuinely new capability (verbatim episodic recall) that the file-memory
does not and should not hold. Not a duplicate.

BUT the danger is precisely at the seam: the file-memory is full of explicit
corrections - "earlier memory said X - that was WRONG" appears repeatedly (the
Hibernate jcache property, the "32 not 34 tests", the "6.6.15 was a snapshot"
entries). Semantic recall over OLD transcripts will happily resurface the superseded X
with high similarity, because the wrong version was stated many times before it was
corrected once. Episodic recall has no notion of "this was later overturned". That is
the #1 functional risk, above any binary-safety concern.

### 2c. Residual safety / cost unknowns

- Binary not line-audited: `mempalace-mcp` runs on Stop/SessionEnd/PreCompact of EVERY
  session (a supply-chain surface that executes automatically, unattended). MIT +
  local-first + no remote endpoint in `.mcp.json` lowers exfiltration risk, but "no
  remote in the manifest" is not "no network syscalls in the binary". It has read
  access to every transcript, which for this user includes Tier-3 allowlisted secret
  echoes that landed in JSONL logs (per CLAUDE.md secrets policy). Anything it indexes,
  it stores in its ChromaDB - so allowlisted-secret values would be embedded into a
  local vector store. Not catastrophic (local), but worth knowing.
- Context cost: 36 always-on MCP tools. Per tool-design.md math (~3-5k tokens for 30
  tools), that is a ~4-5k-token/turn baseline tax on EVERY session - directly opposed
  to the token-savings GOAL. The save-hooks cost nothing per turn; the 36-tool recall
  surface is where the cost lives.
- CPU: local embedding on every save (ChromaDB) - background, but real on a laptop.

### 2d. Low-risk trial plan + verdict

VERDICT: TRIAL the capture, be skeptical of the 36-tool recall surface.

The split that respects both the hard requirement AND the token goal:
1. Enable the mempalace SAVE hooks (Stop/SessionEnd/PreCompact) - free per-turn,
   satisfies "auto-invocable, no manual step", starts building the episodic index now.
2. Do NOT auto-enable all 36 recall tools globally. Either (a) restrict the MCP to the
   Java project scope via a project `.mcp.json`, or (b) prefer the LIGHT recall path in
   section 3c (a ripgrep/BM25 index over the existing JSONL transcripts, invoked
   on-demand by a skill) which gives 80% of the recall for ~0 always-on tokens and no
   unaudited binary in the recall path.
3. Watch for one week: does recall surface CORRECTED-then-superseded facts? If it
   resurfaces a known-wrong entry even once, gate recall behind a freshness/date filter
   or drop the MCP recall surface and keep only capture.
First step: `git clone` mempalace to a scratch dir, read the plugin's `hooks.json` and
the `mempalace-mcp` launch line, enable ONLY the save-hooks, leave the 36-tool MCP
block commented until the light-index alternative (3c) is evaluated head-to-head.

## 3. Memory / context hygiene

### 3a. What the memory files actually look like (measured)

`memory/` = `MEMORY.md` (18.3 KB, a clean INDEX + inline sections) + 5 dedicated files.
The index pattern is GOOD - it links out to dedicated files with "Read before X" gates,
which is exactly the on-demand-load discipline context-engineering preaches.

The problem child: `architecture_simplified_data_initiative.md` = 149,845 bytes
(146 KB, ~38-40k tokens) in only 164 LINES. `awk length` shows single lines up to
9,594 / 7,307 / 6,772 chars. So it is a handful of enormous unsplittable paragraphs.
When the MEMORY.md gate fires ("Read before proposing persistence/cache/asset-loading
changes"), the WHOLE 40k-token blob loads - you cannot Read just the relevant heading
because the content is not line-decomposed. Its own headings (`## Locked names`,
`## Locked technical decisions`, `## Phase order`, `## Post-initiative remaining work`,
`## Open questions deferred`) show it is really 8 topics glued into one file.

`MEMORY.md` body also carries clearly-historical debris: a full "Hibernate Upgrade
5.6 -> 7.3 (DONE)" section, multiple "earlier memory said X - that was WRONG"
paragraphs, and per-migration API notes. Useful once; now mostly archival weight that
loads whenever MEMORY.md loads.

### 3b. CLAUDE.md vs memory split

The split the user already follows is largely correct; refine it:
- CLAUDE.md (global, always-on) = STABLE CONVENTIONS + always-relevant rules: javadoc
  style, exception constructor order, control-flow braces, git-mv rule, secrets tiers,
  workflow-orchestration rules. These are timeless and belong global. Leave them.
- Memory (conditional load) = EVOLVING PROJECT FACTS: locked architecture decisions,
  "do NOT set configUri", the FK/postInit tables. Correct home.
- MISPLACED today: nothing egregious is in CLAUDE.md that should be in memory. The
  drift is the reverse - memory files hold DONE/superseded content that should be
  compacted or moved to a `memory/archive/` subdir so the active gate loads less.

Hygiene actions (concrete):
1. Split `architecture_simplified_data_initiative.md` by its existing `##` headings
   into `memory/architecture/{status,locked-names,locked-decisions,phase-order,
   remaining-work,open-questions}.md`, and reflow the giant lines (one sentence per
   line or short paragraphs) so future loads can Read a single topic. Update the
   MEMORY.md index links to point at the specific sub-file per concern.
2. Move "Hibernate Upgrade (DONE)" and the "earlier memory said WRONG" reconciliations
   into `memory/archive/hibernate-migration.md`; leave a one-line pointer in MEMORY.md
   ("Hibernate is 7.3.0.Final; migration notes archived"). Keeps the correction
   discoverable without paying its weight on every load.
3. Add a one-line staleness marker (date + "SUPERSEDES: <old claim>") to each
   correction so any future recall (grep OR mempalace) can prefer the current fact.

### 3c. Auto-recall: mempalace vs a light transcript index

The re-derivation tax is real and measurable: 3852 greps (vs 34 ripgrep), 869 ad-hoc
tools, 492 "No such file or directory" errors, 220 "has not been read yet". A lot of
that is rediscovering paths and command shapes that a past session already found.

Two ways to cut it:
- Heavy: mempalace (section 2) - semantic, auto, but 36 always-on tools + unaudited
  binary + stale-fact risk.
- Light: a committed `recall` script that ripgreps the existing session JSONLs
  (`~/.claude/projects/W--Workspace-Java-Simplified/**/*.jsonl`, already on disk, ~410
  MB) for a query and returns the top matching user/assistant turns with file+line.
  Zero always-on tokens (invoked on demand via a tiny skill), no new binary, no
  embedding step, and it reads the SAME corpus mempalace would. Drafted as
  `DRAFT-recall-transcripts.sh`.

For THIS user - grep-fluent, token-cost-sensitive, wary of unaudited binaries - the
light index is the higher-ROI first move. It captures the "what command did I use"
recall (which is lexical, not semantic - grep nails it) without the tax. Reserve
mempalace for if/when lexical recall proves insufficient for fuzzier "how did I
approach X" queries.

### 3d. Verdict + first step

VERDICT: ADOPT the hygiene split (it is pure win, no tool needed); ADOPT the light
transcript-recall script; hold mempalace's semantic layer as the section-2 TRIAL.
First step: split the 146 KB architecture file along its `##` headings into
`memory/architecture/` and reflow the >6k-char lines; then drop in
`DRAFT-recall-transcripts.sh` + a 15-line `transcript-recall` skill that points at it.

## 4. Graph-nav MCP (code-review-graph)

### 4a. The install-status problem (it is not live)

The task says "the user already has a code-review-graph MCP". Ground truth (section 0):
it is referenced by three skill files and NOWHERE else - not in the live tool list,
not in `mcp-config.json`, no `.mcp.json` in the Java workspace. So before any rewiring:
confirm it actually exists, is installed, and has an INDEX built over the four family
repos. If the graph is not indexed (or indexes only one repo of the multi-root
workspace), its answers are empty or partial while grep is trivially whole-tree. This
is a precondition, not a detail.

### 4b. grep vs graph vs IntelliJ - token economics

If the graph IS installed and indexed, the token case for specific queries is strong:
- `callers_of` / `callees_of` / `imports_of` / `get_impact_radius` return a
  pre-computed edge set in one small structured payload. The grep equivalent (blast
  radius before a rename) is a project-wide `grep -rn` over `**/*.java` across four
  roots, returning hundreds of noisy lines (comments, string literals, partial-name
  hits) that Claude then re-reads files to disambiguate - the exact loop that inflates
  the 3852-grep / 492-not-found / 220-not-read histograms. get_impact_radius is
  literally the "blast radius" the find-usages skill estimates by hand.
- BUT IntelliJ MCP (`search_symbol` + `get_symbol_info`) is LIVE right now and is
  AST-accurate, overload-resolving, inheritance-following - it already answers
  declaration + usage queries with the same precision as a graph, no indexing lag, no
  freshness question. For declaration/usage lookups the graph adds little over what is
  already installed.
- Where the graph genuinely beats IntelliJ MCP: whole-program IMPACT/FLOW questions
  (`get_impact_radius`, `get_flow`, `get_architecture_overview`, community structure)
  that IntelliJ's per-symbol tools do not directly give in one call.

### 4c. The real lever: under-invocation, not backend choice

The decisive number: `search_symbol` was used ONCE, ever, vs 3852 greps. The
java-symbol-search / java-find-usages skills ALREADY route correctly to IntelliJ MCP -
they are well written. They are simply not being invoked; Claude reaches for grep
reflexively. Swapping the skills' backend from IntelliJ MCP to code-review-graph does
NOT fix that - it just changes where the (rarely-taken) route points. Rewiring is
polishing a path nobody walks.

The high-payoff move is to force the route, not re-choose the destination:
- A `PreToolUse` hook on `Grep` (and on Bash `grep ... --include=*.java` / `rg *.java`)
  that intercepts Java symbol/throw/import/caller patterns and reminds/redirects to the
  symbol-search skill or the live IntelliJ tool. This is a settings.json hook, and the
  update-config skill exists precisely for "whenever X, do Y" harness automation.
- That one hook plausibly moves far more tokens than any backend swap, because it
  attacks the 3852-count behavior directly.

### 4d. Verdict + first step

VERDICT: SKIP rewiring the symbol/usage skills to code-review-graph (unproven install,
and IntelliJ MCP already covers declaration/usage accurately). TRIAL the graph for
IMPACT/FLOW only - IF you first confirm it is installed and indexed across all four
roots - by pointing `refactor-safely` / `debug-issue` (which already reference it) at
it for `get_impact_radius`/`get_flow`, keeping symbol+usage on IntelliJ MCP.
First step (do this BEFORE any skill edit): run `/mcp` and grep the live registry for
the graph server; if absent, the "already has it" premise is false and the whole item
collapses to "install-or-drop". In parallel, prototype the Grep->symbol-search
PreToolUse hook - that is the real token lever regardless of which backend wins.

## 5. Cross-cutting conclusions

1. The single biggest token lever in all four items is NOT a new server - it is the
   Grep->routed-search under-invocation gap (1 search_symbol vs 3852 greps). Every item
   here is downstream of that. Fix routing (a PreToolUse hook) before buying capability.
2. Always-on MCP schema is a per-turn tax paid in every session; scope any new MCP to
   the Java workspace via project `.mcp.json` so mixed-work sessions do not pay for it.
   This applies to both the simplified-tools MCP (#1) and mempalace's 36 tools (#2).
3. Do not wrap prompt-routing skills as MCP tools - it converts a free on-demand skill
   into an always-listed schema for zero capability. Only deterministic, hand-re-
   authored operations earn an MCP tool.
4. Two of the three claimed backends are not actually usable right now (code-review-
   graph phantom, jdtls-lsp dormant/no binary). Verify-before-build is a recurring
   theme; several recommendations here start with "run /mcp and confirm".
5. Ranked adopt order by ROI: (a) memory hygiene split + light transcript recall
   (free, immediate), (b) Grep->symbol PreToolUse hook (biggest behavioral lever),
   (c) small project-scoped simplified-tools MCP for gradle_verify/test_tally, (d)
   mempalace save-hooks trial, (e) graph impact/flow trial IF installed. Skip: mass
   skills->MCP conversion, skill backend rewiring to the phantom graph.

## 6. Deliverables produced
- `DRAFT-mcp-memory-recommendations.md` - the four verdicts + first steps (primary).
- `DRAFT-simplified-tools-mcp-server.py` - minimal FastMCP stdio scaffold (#1 arch).
- `DRAFT-simplified-tools.mcp.json` - project-scoped registration for the above.
- `DRAFT-recall-transcripts.sh` - light on-demand transcript recall (#2/#3 lighter path).
