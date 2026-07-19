# MCP / Memory / Context Recommendations (A5)

Four decisions, each: verdict (adopt / trial / skip) + the concrete first step.
Backed by measured runtime facts, not assumptions - see the ground-truth box below.

> Ground truth measured this session (matters for #1 and #4):
> - Live MCP servers: IntelliJ_IDE, DeepWiki, Gmail/Calendar/Drive, Postman. Nothing else.
> - `code-review-graph` MCP: referenced by 3 skill files only; NOT in the live tool
>   list, NOT in `~/.claude/data/mcp-config.json`, no `.mcp.json` in the Java workspace.
>   => phantom / not installed. Treat "already has it" as unverified.
> - `jdtls-lsp` plugin: enabled in settings.json, but `jdtls` binary not on PATH => dormant.
> - `search_symbol` used 1x ever vs 3852 greps. Routing, not backend, is the bottleneck.
> - Toolchain present: Python 3.14.3, Node v22.15.0, JDK 25/21/17/8, fastmcp 1.0.

---

## 1. Skills-as-MCP - VERDICT: TRIAL (narrow)

**Question**: convert the Java skills (bulk-rename, javadoc-normalize, the auditors, the
new file-mover) into one local MCP server?

**Finding**: the skills split into two kinds. Most are PROMPT-ROUTING - they carry no
logic, they just tell Claude to call an existing IntelliJ MCP tool with the right
pattern (`java-bulk-rename` -> `rename_refactoring`; `java-symbol-search`/`find-usages`
-> `search_symbol`/`get_symbol_info`; the three auditors -> `get_file_problems`). Only a
few carry REAL deterministic logic (`javadoc-normalize`'s 23 KB `normalize.py`) or are
hand-re-authored constantly (the gradle-verify noise-strip shape, the test-XML tally,
the import reorderer - part of the 869 ad-hoc-tool events).

**Why not "convert everything"**: `references/tool-design.md` is explicit - every MCP
tool schema is tokens spent EVERY turn (30 tools ~= 3-5k tokens/turn baseline). A skill
costs one index line until invoked. Wrapping a routing skill as an MCP tool pays the
per-turn schema tax to forward to another MCP for zero new capability. That is a net
token loss and cuts against the whole goal.

**Decision rule**:
- Routing-only skill -> STAY a skill (bulk-rename, symbol-search, find-usages,
  import/modifier/record auditors, exception-class-gen, gradle-verify-gate).
- Deterministic + hand-re-authored-often -> MCP tool, but PROJECT-SCOPED so the schema
  only loads under the Java tree.
- Deterministic + already fine as a Bash-called script -> stay a committed script
  (`normalize.py` today), optionally EXPOSED through the project MCP for typed calls.

**Minimal architecture** (drafted in `DRAFT-simplified-tools-mcp-server.py` +
`DRAFT-simplified-tools.mcp.json`): ONE FastMCP stdio server `simplified-tools`,
registered at `W:/Workspace/Java/Simplified/.mcp.json` (loads only there), 3 core tools:
- `gradle_verify(module, tasks, tail)` - runs the gate, strips `incubating|warning|
  Deprecated`, returns `{exit_code, failed_first, tail}`. Kills the #1 hand-rewritten
  shape (`cd MODULE && ./gradlew ... | grep -vE ... | tail -N; echo PIPESTATUS`).
- `test_tally(module)` - parses `build/test-results/test/*.xml`, returns
  `{total, passed, failed, skipped, failing_tests[]}`. Kills the recurring XML tally.
- `javadoc_normalize(paths, fix, scope, prefix)` - shells the EXISTING `normalize.py`;
  audit is `readOnlyHint`, `--fix` is `destructiveHint`.
- (optional later) `sort_imports`, `git_move_java` (the file-mover) once IDE-accurate
  ordering / the move rule are pinned.

**Effort**: ~2-3 h AI-assisted elapsed (you driving + me) to scaffold + wire the 3
tools + `.mcp.json` + `/mcp` smoke test; ~1-1.5 days human-developer unaided.

**First step**: `pip install fastmcp`; drop `DRAFT-simplified-tools-mcp-server.py` at
`W:/Workspace/Java/Simplified/tools/` and `DRAFT-simplified-tools.mcp.json` (renamed to
`.mcp.json`) at the workspace root; run `/mcp` to confirm `simplified-tools` loads;
exercise `gradle_verify` on one module. Roll back to skills+scripts if after a week the
tools are not displacing the hand-authored shapes.

## 2. Mempalace - VERDICT: TRIAL (capture yes, 36-tool recall no)

**Fit**: passes the hard requirement cleanly. Its hooks (Stop / SessionEnd / PreCompact)
auto-save with no human step; the MCP recall tools are model-invocable. This is the
auto-invocable memory the setup asks for and that hand-edited file-memory is not.

**Adds value, not a duplicate**: file-memory (`MEMORY.md` + dedicated files) is curated,
semantic, human-vetted (locked decisions, "do NOT rename X"). Mempalace is episodic,
verbatim, high-volume - it indexes the ~410 MB of past transcripts and recalls the long
tail ("what grep filtered the gradle noise", "how did I fix the jitpack poll") that is
being RE-DERIVED today (869 ad-hoc tools, 3852 greps). Different layer.

**The real risk is functional, not just supply-chain**: the file-memory is full of
"earlier memory said X - that was WRONG" corrections (the Hibernate jcache property,
"32 not 34 tests"). Semantic recall over OLD transcripts will resurface the superseded X
with high similarity, because the wrong version was stated many times before one
correction. Episodic recall has no "later overturned" signal.

**Other unknowns to price in**:
- `mempalace-mcp` binary not line-audited and runs unattended on every session end /
  compaction; it reads every transcript (which for this user includes Tier-3
  allowlisted-secret echoes that landed in JSONL) and embeds them into local ChromaDB.
- 36 always-on MCP tools ~= 4-5k tokens/turn baseline - directly against the token goal.
  The save-HOOKS cost nothing per turn; the cost is entirely the recall tool surface.

**Trial plan**:
1. Enable ONLY the save-hooks (free per-turn, satisfies the requirement, starts the
   index now).
2. Do NOT globally enable the 36-tool recall MCP. First evaluate the LIGHT recall path
   (`DRAFT-recall-transcripts.sh`, item 3) head-to-head - same corpus, ~0 always-on
   tokens, no unaudited binary in the recall path. If lexical recall proves too shallow
   for fuzzy "how did I approach X" queries, THEN enable mempalace recall, scoped to the
   Java project `.mcp.json`, not globally.
3. Watch one week for stale-fact resurfacing; if a known-wrong entry surfaces even once,
   gate recall by date/freshness or drop the recall surface and keep only capture.

**First step**: clone mempalace to scratch, read its plugin `hooks.json` + the
`mempalace-mcp` launch line, enable the save-hooks only, leave the MCP block commented.

## 3. Memory / context hygiene - VERDICT: ADOPT

**Finding**: `MEMORY.md` (18.3 KB) is a good on-demand INDEX with "Read before X" gates.
The problem is `architecture_simplified_data_initiative.md` = 146 KB (~38-40k tokens) in
164 lines, with single lines up to 9,594 chars. Its own `##` headings show it is 8
topics glued together. When the MEMORY.md gate fires, the whole 40k-token blob loads and
you cannot Read just the relevant heading, because the content is not line-decomposed.
`MEMORY.md` also carries done/superseded content (full "Hibernate Upgrade (DONE)"
section, several "earlier memory said WRONG" reconciliations) that loads every time.

**CLAUDE.md vs memory split** (already mostly right - refine, do not overhaul):
- Keep in global CLAUDE.md: stable conventions (javadoc, exception order, control-flow
  braces, git-mv, secrets tiers, workflow rules). Timeless.
- Keep in memory (conditional load): evolving project facts (locked decisions, FK/postInit
  tables, "do NOT set configUri").
- The drift is the reverse of the usual: memory holds ARCHIVAL content that should be
  compacted out of the hot path, not CLAUDE.md holding project trivia.

**Concrete hygiene actions**:
1. Split the 146 KB architecture file along its `##` headings into
   `memory/architecture/{status,locked-names,locked-decisions,phase-order,
   remaining-work,open-questions}.md`, reflow the >6k-char lines (one sentence / short
   paragraph per line), and point each MEMORY.md gate at the specific sub-file per concern.
2. Move "Hibernate Upgrade (DONE)" + the "earlier said WRONG" reconciliations into
   `memory/archive/hibernate-migration.md`; leave a one-line pointer in MEMORY.md.
3. Tag each correction with `SUPERSEDES: <old claim>` + date so any recall prefers the
   current fact.

**Auto-recall**: the re-derivation tax is measurable (3852 greps, 869 ad-hoc tools, 492
"No such file", 220 "not read yet") - much of it rediscovering paths/commands a past
session already found. Prefer the LIGHT path first: `DRAFT-recall-transcripts.sh`
ripgreps the existing session JSONLs on demand (zero always-on tokens, no new binary,
same corpus mempalace would use). "What command did I use" recall is lexical - grep nails
it. Reserve mempalace's semantic layer for fuzzier queries (item 2).

**First step**: split the architecture file into `memory/architecture/` and reflow its
long lines; drop in `DRAFT-recall-transcripts.sh` + a ~15-line `transcript-recall` skill
pointing at it. No new server required - pure win.

## 4. Graph-nav MCP (code-review-graph) - VERDICT: SKIP rewire / TRIAL impact-only

**Precondition problem**: "the user already has a code-review-graph MCP" is UNVERIFIED.
It is referenced by 3 skill files only; it is in no live tool list and no config. Before
any rewiring, run `/mcp` and confirm the server exists AND has an index over all four
family roots. If it indexes one repo of a four-root workspace, its answers are partial
while grep is trivially whole-tree.

**Token economics** (if installed + indexed):
- `callers_of` / `imports_of` / `get_impact_radius` return a pre-computed edge set in one
  small payload. The grep equivalent (blast radius before a rename) is a four-root
  `grep -rn **/*.java` returning noisy hits that force file re-reads - the exact loop
  behind the 3852-grep / 492-not-found / 220-not-read histograms. `get_impact_radius` is
  literally the "blast radius" the find-usages skill estimates by hand.
- BUT IntelliJ MCP (`search_symbol` + `get_symbol_info`) is LIVE now, AST-accurate,
  overload/inheritance-resolving, no index-freshness question. For declaration + usage
  lookups it already matches a graph. The graph only clearly WINS on whole-program
  impact/flow (`get_impact_radius`, `get_flow`, `get_architecture_overview`).

**The real lever is under-invocation, not backend**: `search_symbol` used 1x ever vs
3852 greps. The symbol-search / find-usages skills ALREADY route correctly to IntelliJ
MCP - they are simply not invoked; Claude greps reflexively. Repointing them at the
graph changes the destination of a route nobody takes. Instead FORCE the route: a
`PreToolUse` hook on `Grep` (and Bash `grep --include=*.java` / `rg *.java`) that
intercepts Java symbol/throw/import/caller patterns and redirects to the symbol-search
skill or the live IntelliJ tool. Use the `update-config` skill (it owns "whenever X do
Y" harness automation). That one hook plausibly moves more tokens than any backend swap.

**Decision**:
- SKIP rewiring `java-symbol-search` / `java-find-usages` to code-review-graph -
  unproven install, and IntelliJ MCP already covers those accurately.
- TRIAL the graph for IMPACT/FLOW only, and only after confirming install+index, by
  letting `refactor-safely` / `debug-issue` (which already reference it) drive
  `get_impact_radius` / `get_flow`; keep symbol+usage on IntelliJ MCP.
- Separately, note `jdtls-lsp` is enabled but its `jdtls` binary is not installed - either
  install it (native LSP nav, no per-turn schema cost) or disable the dead plugin.

**First step**: run `/mcp`; if the graph server is absent the premise is false and this
item collapses to "install-or-drop". In parallel, prototype the Grep->symbol-search
PreToolUse hook - the real token lever regardless of backend.

---

## Priority order (by ROI)

1. Memory hygiene split + `DRAFT-recall-transcripts.sh` (item 3) - free, immediate, pure win.
2. Grep -> symbol-search `PreToolUse` hook (item 4) - biggest behavioral token lever.
3. Project-scoped `simplified-tools` MCP: `gradle_verify` + `test_tally` + expose
   `normalize.py` (item 1) - deterministic high-frequency shapes, scoped so no global tax.
4. Mempalace save-hooks trial (item 2) - capture now, defer the 36-tool recall surface.
5. Graph impact/flow trial (item 4) - only if `/mcp` confirms it is installed + indexed.

Skip: mass skills -> MCP conversion; rewiring symbol/usage skills to the phantom graph.

## Files in this drop
- `DRAFT-mcp-memory-recommendations.md` (this file) - the four verdicts + first steps.
- `DRAFT-simplified-tools-mcp-server.py` - minimal FastMCP stdio scaffold (item 1).
- `DRAFT-simplified-tools.mcp.json` - project-scoped registration (rename to `.mcp.json`
  at `W:/Workspace/Java/Simplified/`).
- `DRAFT-recall-transcripts.sh` - light on-demand transcript recall (items 2/3).
- Analysis backing all of the above: `notes/A5-mcp-memory.md`.

## Reconciliation with the plain-script drafts (other A-agents)

Sibling agents in this run already produced `DRAFT-gwverify.sh` (gradle noise-strip +
exit-code), `DRAFT-jtally.py` (JUnit tally), and `DRAFT-reorder_imports.py` (IDE-order
import sort) as PLAIN COMMITTED SCRIPTS. That is the correct default per item 1's
decision rule and is evidence FOR scripts-over-MCP: these operations are deterministic
and work fine as Bash-invoked scripts at zero per-turn cost.

The `simplified-tools` MCP is therefore OPTIONAL, and only earns its always-on schema
if typed, cannot-be-forgotten invocation measurably beats the scripts in practice. If
built, it should SHELL OUT to those sibling scripts rather than reimplement them (my
scaffold inlines the logic for standalone testability - swap the bodies to call
`DRAFT-gwverify.sh` / `DRAFT-jtally.py` / `DRAFT-reorder_imports.py` once those land).
Net: adopt the scripts now; treat the MCP as a thin typed veneer to trial later, not a
prerequisite.
