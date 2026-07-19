# Token-Optimization Report - Simplified Java Workspace

How to spend fewer tokens driving the four family roots under
`W:/Workspace/Java/Simplified/` (SkyBlock-Simplified, Simplified-Dev,
Simplified-Api, Minecraft-Library). Grounded in a corpus of **33,627 recorded
shell commands**, ~**1,043** avoidable failed tool calls, **869** throwaway-tool
authoring events, and the 16 user-authored skills under `~/.claude/skills`.

Every recommendation below names (a) the finding with real numbers, (b) the
recommendation, (c) the exact deliverable file to install and where, and (d) a
rough, honestly-bounded token saving. All deliverables already exist as
`DRAFT-*` files in this session's scratchpad; the install checklist at the end
maps each to its destination.

---

## 0. Thesis - where the tokens actually go

The waste is **not** spread evenly across the work. It concentrates in a handful
of mechanical shapes that get re-typed and re-filtered hundreds of times each,
plus a long tail of wrong-assumption failures that each cost a full recovery
turn. Three levers dominate, in order:

1. **One reinvented command shape.** The gradle verify incantation -
   `cd "W:/.../module" && ./gradlew <task> -q 2>&1 | grep -vE "incubating|warning" | tail -N ; echo ${PIPESTATUS[0]}` -
   is reinvented **~1,500 times** with **8 drifting filter spellings** and 7
   different `tail -N` widths. Each instance costs ~50 generated tokens to author
   plus ~300-500 tokens to read back a noisy `tail -40`. Collapsing it to
   `gw ar test` (author ~4 tokens, read back one status line ~20 tokens) is the
   single biggest win: **~0.5M-0.9M tokens** of the estimated ~0.9M-1.1M total
   avoidable command waste, ~80% of the corpus's command-token bloat.

2. **~1,043 avoidable failed tool calls**, 97% in three mechanical buckets:
   path-guessing (**646**, 62%), Edit/Write-before-Read (**223**, 21%),
   stale/non-unique `old_string` (**164**, 16%). Each failure is a wasted call
   plus a recovery turn. All three have low-context, mechanical countermeasures
   (a Project Map table, a read gate, an exact-bytes rule) that fit in ~61 lines
   of CLAUDE.md.

3. **869 throwaway-tool authoring events** - the same JUnit-XML tally, jitpack
   poll loop, and (wrongly-implemented) import sorter re-written from scratch
   over and over. Most deserve a committed helper; a few (the naive import
   sorter) must be replaced by a correct one, not committed as-is.

The correct architecture for all three is the same: **cheap, always-available
committed scripts + dense CLAUDE.md facts**, not new always-on MCP tool schemas
(which cost ~3-5k tokens/turn each in baseline overhead). The single highest-ROI
line-for-line addition is the **Project Map** (kills the largest failure bucket);
the single highest-ROI behavioral change is routing the gradle shape through
`gw`. Everything else is amortized cleanup around those two.

Rough envelope of what is recoverable: **~0.9M-1.1M** command-authoring/read-back
tokens across the recorded corpus, **~2,000+** avoided failed tool calls and
their recovery turns, and a forward-looking **20-40%** reduction in
navigation/verification token spend on refactor-heavy sessions once the routing
is enforced. These are order-of-magnitude estimates from command-shape counts,
not measured A/B deltas - treat them as directional.

## 1. Existing-skill upgrades (16 skills audited)

All 16 loaded skills (plus 4 non-loading loose `.md` files) were audited against
the corpus. The highest-value gaps are mechanical. Full per-skill patches -
ready-to-install SKILL.md replacements and exact diffs - are in
**`DRAFT-skill-patches.md`**. The seven changes below are ordered by hit rate.

### 1.1 gradle-verify-gate - ship the missing runner (HIGH)

**Finding.** The skill *names* `./gradlew compileJava test` but ships no reusable
noise-stripping runner, so the gate was hand-authored **240 times** with drifting
`tail -N` widths, drifting filter fragments, and buggy `|` vs `&&` chaining (the
`${PIPESTATUS}`-through-a-pipe trap loses the real exit code).

**Recommendation.** Install a real runner that writes gradle output to a temp
log, captures the true exit code, strips one canonical noise set, surfaces only
the first N signal lines, and ends with a greppable `GATE: PASS|FAIL rc=<n>`
trailer. Add the module-root table so the `-d` value is never guessed. This
coordinates with (does not duplicate) the A1 `gw` wrapper - the skill only
requires *something* that emits the `GATE:` trailer.

**Deliverable.** `DRAFT-gwverify.sh` -> `~/.claude/skills/gradle-verify-gate/gwverify.sh`
(`chmod +x`); patch the "## Standard invocation" block per `DRAFT-skill-patches.md` §1.

**Saving.** Removes ~200-400 re-typed/re-filtered tokens per invocation across
the 240-variant shape - the largest single skill win, and it overlaps the §5.1
gradle lever.

### 1.2 java-exception-class-gen - emit `final` on leaf children (HIGH)

**Finding.** The child template emits `public class FooBarException` with no
`final`, but leaf exceptions are expected `final` (the `java-modifier-audit`
convention; 24/64 tree classes are already final). Every generated child then
forces a follow-up modifier-audit -> Read -> Edit -> re-gate round-trip.

**Recommendation.** In the CHILD template only, emit `public final class`. Leave
the ROOT template as `public class ... extends RuntimeException` (a `final` root
cannot be extended). Adding `final` does not touch the constructor order or the
`super(...)` argument reversal, so CLAUDE.md `## Exceptions` conformance is
preserved. Update the `java-modifier-audit` cross-ref so it now *verifies*
children rather than being expected to catch the miss.

**Deliverable.** Patch `~/.claude/skills/java-exception-class-gen/SKILL.md` per
`DRAFT-skill-patches.md` §2 (template line + new "## Modifiers" section).

**Saving.** Removes one full audit+Read+Edit+re-gate round-trip (~1-3k tokens)
per generated exception child.

### 1.3 javadoc-normalize - fix the `_inject_imports` misroute + defer reorder (HIGH)

**Finding.** A real correctness bug: `_top_prefix()` strips a leading `static `,
so `import static java...` reports prefix `java`. A newly FQN-auto-imported plain
`java.util.X` can then match a static-only group and, after `g.sort()`, land
*inside the static block* - wrong per IntelliJ Default. Separately, the skill has
no standalone "reorder existing imports" entry point, which is exactly the
recurring gap that keeps regenerating the (incorrect) ad-hoc `sortimports.py`.

**Recommendation.** (a) Add an `_is_static` helper and match new imports only
against non-static existing lines (exact diff in `DRAFT-skill-patches.md` §3a),
plus a regression fixture. (b) Add a short "## Reordering existing imports (out of
scope)" section that points reorder needs at the A4 `reorder_imports.py` +
`import-order.md` rather than growing a second reorderer here.

**Deliverable.** Patch `normalize.py` (`~L329-368`) and `SKILL.md` per
`DRAFT-skill-patches.md` §3.

**Saving.** Correctness (prevents a dropped-out-of-scope import) + retires the
repeated `sortimports.py` re-authoring (~1-3k tokens per occurrence).

### 1.4 java-bulk-rename - own the move space, real no-IDE recipe (HIGH)

**Finding.** The skill fires **32 times** against **1,037 `sed`** and **63
`git mv`** in the corpus - it under-triggers badly and abdicates the *move*
space. The real recurring shape is a MOVE (git mv + fix package decl + per-type
FQN sed + reorder imports) done by hand, and the skill treats `sed` as a degraded
footnote while over-relying on `reformat_file` (used 2x total).

**Recommendation.** Broaden the trigger phrases to cover "move/relocate/git mv";
add a "## Moves" section routing to the A4 `java-file-mover` skill; replace the
weak "## Fallback" with a concrete word-boundary no-IDE rename recipe; soften the
reformat step to IDE-attached only. See `DRAFT-skill-patches.md` §4.

**Deliverable.** Patch `~/.claude/skills/java-bulk-rename/SKILL.md`.

**Saving.** Converts a large fraction of the 1,037 hand-`sed` events into a
scripted, gated path; overlaps §2.3 (file-mover).

### 1.5 java-symbol-search / java-find-usages - invert to Grep-first (MEDIUM-HIGH)

**Finding.** Both skills route primarily to IntelliJ `search_symbol` /
`get_symbol_info`, which appear **~1x in the entire corpus**; `Grep` ran **892x**
and shell `grep` **3,852x**. The "IDE attached" assumption does not hold for these
two MCP tools (unlike `get_file_problems`, used 148x). The Grep fallback is the
real primary path but is written as a thin footnote. Note `rg` is barely present
(34 vs grep 3,852), so recipes must use `grep`/the harness Grep tool, not ripgrep.

**Recommendation.** Invert the framing to **Grep-first** with concrete
ready-to-run recipes (throw sites, subclasses, importers, simple-name usages),
and demote the MCP table to an "if the IDE is attached, these give AST-exact
results" precision upgrade. Delete the unverifiable "reads vs writes in some IDE
versions" claim. See `DRAFT-skill-patches.md` §5.

**Deliverable.** Patch both `~/.claude/skills/java-symbol-search/SKILL.md` and
`~/.claude/skills/java-find-usages/SKILL.md`.

**Saving.** Saves the post-processing of ~890 hand-built greps by giving the
correct recipe up front; complements the §7.4 routing hook.

### 1.6 context-engineering + pattern-recognition - dead stores, auto-fire (MEDIUM)

**Finding.** Both auto-fire every session but reference **files that do not
exist**: `~/.claude/data/pattern-index.json` and
`~/.claude/scripts/calculate-confidence.sh`. The entire "NEW v3.1"
suggestion/confidence engine (Steps 5-7, ~250 lines) no-ops, and neither skill
references the real project memory (`MEMORY.md`). They also carry emoji against
the CLAUDE.md no-emoji rule.

**Recommendation.** Delete the dead Steps 5-7 (recommended over creating the
backing files); repoint `knowledge-core.md` mentions at the real
`~/.claude/projects/W--Workspace-Java-Simplified/memory/MEMORY.md`; narrow
auto-invoke to explicit user request; de-emoji. `context-engineering` also
duplicates the built-in `context` slash command (same 39%/84% claims) - keep the
command, demote the skill to `auto_invoke: false`. See `DRAFT-skill-patches.md` §9.

**Deliverable.** Surgical edits to both SKILL.md files (not rewrites).

**Saving.** Removes ~250 never-firing instruction lines from every session's
skill surface and stops pointing learning at a store nobody reads.

### 1.7 Lower-value skill fixes (LOW)

From `DRAFT-skill-patches.md` §6-11, install opportunistically:

- **java-import-audit** (§6): add a triage row routing "imports out of order" to
  `reorder_imports.py` (the wrong-order gap); note it warns against the old
  `sortimports.py`.
- **java-modifier-audit** (§7): drop the design-scaffolding ref
  (`scan-the-codebase-for-composed-tarjan.md line 19`) that violates the CLAUDE.md
  no-scaffolding rule; state the standing "leaf exceptions are `final`" convention
  inline. Update the exception-gen cross-ref (now that children ship `final`).
- **java-record-audit** (§8): widen the discovery regex from
  `^public\s+record\s+\w+` to `\brecord\s+\w+\s*\(` (the current form misses
  non-public / nested / `final record` / no-modifier records).
- **Four dead loose files** (§10): `debug-issue.md`, `explore-codebase.md`,
  `review-changes.md`, `refactor-safely.md` sit directly under `skills/` with
  Title-Case `name:` and never load. Probe `code-review-graph` with one
  `semantic_search_nodes` call; if it errors, delete all four; if it answers,
  promote each to `<name>/SKILL.md` with kebab-case names and trigger phrases.
- **jmh-regression-gate** (§11): add a zero-pair guard - a run where no benchmarks
  pair currently exits 0 (false green); return 2 with a message instead.

## 2. The java-file-mover skill + faithful import reorderer

A non-trivial file move today costs symbol-discovery greps + several manual
Edits + import fixes surfaced only by repeated failing compiles (the §5.1 gradle
shape again) + re-authoring a throwaway import sorter - roughly **15-40k tokens**
per move. Two deliverables collapse this: a faithful import reorderer and a
single move-orchestrating skill.

### 2.1 The empirically-derived IntelliJ Default import order

**Finding.** Both `.idea/codeStyles/codeStyleConfig.xml` pin
`PREFERRED_PROJECT_CODE_STYLE=Default` with **no** custom `IMPORT_LAYOUT_TABLE`
and **no** `.editorconfig` anywhere, so the authoritative order *is* the IntelliJ
built-in Default scheme. It was reverse-engineered from **48 committed `.java`
files** across all four family roots (8 read in full + 40 stratified). The order
is three blank-separated groups:

1. all non-static, non-`java`/`javax` imports, ASCII-sorted by full path;
2. `javax.*` block **then** `java.*` block (NOT alphabetical - a flat sort would
   put `java.` before `javax` because `.`=46 < `x`=120), each independently sorted;
3. all static imports, one trailing group regardless of package.

The sort is **flat-string ASCII `String.compareTo`** (case-sensitive), proven by
`ApacheClientFactory.java` L16-19 where upper-cased class segments (`Http...`,
`URIScheme`) sort before lower-cased sub-packages (`config`, `protocol`) at equal
depth. Only exact top segments `java` and `javax` are special-cased; `jakarta.*`,
`api.*`, `com`, `net`, `lib`, etc. all sort alphabetically in group 1. Wildcards
are preserved verbatim and sorted in place by their path (the `.*` included).

Both existing ad-hoc tools get this wrong: the naive `sortimports.py` interleaves
`import static` at the `s` position and never crosses blank-line groups; and
`javadoc-normalize`'s `_inject_imports` inverts java-vs-javax (§1.3). Full rule
list with per-file citations is in **`DRAFT-import-order.md`**.

### 2.2 reorder_imports.py - validated faithful and idempotent

**Finding / validation.** `DRAFT-reorder_imports.py` is IDE-independent,
idempotent, CRLF/newline-safe, wildcard-preserving, and comment-safe. It was run
over **87 real files**: it left already-correct files untouched (74/87 reported
as no-ops by `--check`), fixed real drift correctly, and never corrupted a file.
The idempotent `--check` mode also prevents wasted re-verify cycles on
already-correct files.

**Recommendation.** Install it as the single canonical reorderer and route all
"imports in the wrong order" needs to it (from `java-import-audit` §1.7 and
`javadoc-normalize` §1.3). Do **not** commit the naive `sortimports.py`.

**Deliverable.** `DRAFT-reorder_imports.py` -> `~/.claude/bin/reorder_imports.py`
(or the file-mover skill dir); `DRAFT-import-order.md` -> alongside it as the
no-IDE reference.

**Saving.** Eliminating the recurring `sortimports.py` re-authoring saves ~1-3k
tokens per occurrence; the idempotent `--check` avoids re-verify churn on
correct files.

### 2.3 The java-file-mover skill

**Finding.** Moves are the un-owned space (§1.4): 63 `git mv` + 1,037 `sed`
against 32 bulk-rename invocations. The pivotal fact is the repo topology - each
module has its **own `.git`** (`persistence/.git`, `asset-renderer/.git`, ...),
so a cross-module move crosses a repo boundary and `git mv` cannot span it, and
it additionally needs a Gradle `includeBuild` dependency edge in the destination.

**Recommendation.** A single `java-file-mover` skill that prefers IntelliJ
`rename_refactoring` / Move (atomic, type-aware) and falls back to a deterministic
`git mv` (within the owning module repo) -> rewrite `package` -> rewrite every
`import <oldfqn>;` -> `reorder_imports.py` -> `gwverify.sh` pipeline. Its decision
tree pivots on the confirmed per-module git topology and covers ~100% of
move/relocate/rename cases. It re-uses `java-symbol-search`/`java-find-usages` for
discovery, `reorder_imports.py` for imports, `javadoc-normalize` for FQN fixes,
and `gradle-verify-gate` for the gate - no duplication.

**Deliverable.** `DRAFT-java-file-mover-SKILL.md` ->
`~/.claude/skills/java-file-mover/SKILL.md`.

**Saving.** ~60-75% reduction per move (~10-30k saved each); across a multi-move
refactor session, ~40-120k tokens.

## 3. Mempalace verdict - TRIAL (capture yes, 36-tool recall no)

**Fit.** Mempalace passes the hard requirement cleanly: it is **auto-invocable**
with no human step. Its plugin hooks (Stop / save every 15 msgs, SessionEnd,
PreCompact) save automatically, and its MCP tools are model-invocable. It is
local-first (bundled ChromaDB, no API key), 57.5k-star MIT. As an *episodic,
verbatim* layer it genuinely complements the curated, human-vetted file-memory
(`MEMORY.md`) - it would index the ~410 MB of past transcripts and recall the
long tail ("what grep filtered the gradle noise", "how I fixed the jitpack poll")
that is being **re-derived today** (869 ad-hoc tools, 3,852 greps).

**Two real risks, priced honestly.**

1. **Functional, not just supply-chain.** The file-memory is full of "earlier
   memory said X - that was WRONG" corrections (the Hibernate jcache property;
   "32 not 34 tests"). Semantic recall over old transcripts will resurface the
   superseded X with *high* similarity, because the wrong version was stated many
   times before the single correction. Episodic recall carries no "later
   overturned" signal.
2. **Token cost of the recall surface.** The **36 always-on MCP tools** add ~4-5k
   tokens/turn baseline - directly against the goal. The save-*hooks* cost
   nothing per turn; the entire cost is the recall tool surface. (Also: the
   `mempalace-mcp` binary is unaudited and reads every transcript, including this
   user's Tier-3 allowlisted-secret echoes that landed in JSONL.)

**Recommendation - TRIAL, split the two halves.**

- Enable **only the save-hooks** now (free per-turn, satisfies the requirement,
  starts the index).
- Do **not** globally enable the 36-tool recall MCP. First evaluate the light
  lexical recall (§7.2, `DRAFT-recall-transcripts.sh`) head-to-head on the same
  corpus at ~0 always-on tokens. Only if lexical recall proves too shallow for
  fuzzy "how did I approach X" queries, enable mempalace recall **scoped to the
  Java project `.mcp.json`**, never globally.
- Watch one week for stale-fact resurfacing; if a known-wrong entry surfaces even
  once, gate recall by freshness or drop recall and keep only capture.

**Deliverable / first step.** Clone mempalace to scratch, read its plugin
`hooks.json` + the `mempalace-mcp` launch line, enable the save-hooks only, leave
the MCP block commented. Backing analysis: `DRAFT-mcp-memory-recommendations.md`
§2, `notes/A5-mcp-memory.md`.

**Saving.** Net-*positive* only in the capture-only configuration. A global
36-tool recall surface is net-**negative** (~4-5k tokens/turn) and is the thing to
avoid.

## 4. Skills-as-MCP verdict - TRIAL (narrow)

**Finding.** Most Java skills are **pure prompt-routing** - they carry no logic,
they just tell Claude to call an existing IntelliJ MCP tool with the right pattern
(`java-bulk-rename` -> `rename_refactoring`; symbol-search/find-usages ->
`search_symbol`/`get_symbol_info`; the three auditors -> `get_file_problems`).
Re-wrapping a routing skill as an MCP tool pays the per-turn schema tax
(~3-5k tokens/turn for ~30 always-on schemas, per `references/tool-design.md`) to
forward to another MCP for **zero new capability** - a net token loss. A skill
costs one index line until invoked.

Only a few skills carry **deterministic, hand-re-authored-often** logic that
justifies a typed tool: the gradle-verify noise-strip shape, the test-XML tally,
`normalize.py`. And those already work fine as **committed Bash scripts** at zero
per-turn cost.

**Recommendation - decision rule, not a blanket conversion.**

- Routing-only skill -> **stay a skill** (bulk-rename, symbol-search,
  find-usages, the auditors, exception-class-gen, gradle-verify-gate).
- Deterministic + already-fine-as-a-script -> **stay a committed script** (`gw`,
  `jtally.py`, `reorder_imports.py`, `normalize.py`), optionally exposed later.
- Deterministic + hand-re-authored-often + typed-invocation-matters -> a small
  **project-scoped** MCP so the schema loads only under the Java tree.

**Minimal architecture (optional, trial).** One FastMCP stdio server
`simplified-tools`, registered at `W:/Workspace/Java/Simplified/.mcp.json` (loads
only there), three tools: `gradle_verify(module, tasks, tail)`,
`test_tally(module)`, `javadoc_normalize(paths, fix, ...)`. Crucially it should
**shell out** to the committed `gwverify.sh` / `jtally.py` / `normalize.py` rather
than reimplement them. The MCP is a thin typed veneer to trial *later*, not a
prerequisite - the scripts capture the savings today.

**Deliverable.** `DRAFT-simplified-tools-mcp-server.py` +
`DRAFT-simplified-tools.mcp.json` (rename to `.mcp.json` at the workspace root).
Effort ~2-3 h AI-assisted. Analysis: `DRAFT-mcp-memory-recommendations.md` §1.

**Saving.** Neutral-to-slightly-positive if built (typed, cannot-be-forgotten
invocation); the real savings are in the underlying scripts. A broad
skills->MCP conversion is the net-negative trap to avoid.

## 5. sed / grep / bash + tool-call chaining wins

Command-verb histogram over 33,627 shell commands: `echo` 4,821, `grep` 3,852
(ripgrep `rg` only 34), `head` 2,353, `tail` 1,888, `gradlew` ~2,300, `git`
1,658, `sed` 1,037, `ls` 1,128, `find` 754, `wc` 633. Chaining `&&` 5,036, pipe
5,029. The waste is not in any one verb - it is in the *recurring assembled
shapes* those verbs form. Three deliverables (`gw`, `jtally.py`, `modules.sh`)
plus a CLAUDE.md hygiene section retire the top shapes; all three were validated
live against the asset-renderer module.

### 5.1 The one incantation reinvented ~1,500 times

**Finding.** The single most-repeated real command shape, in hundreds of
near-identical variants:

```
cd "W:/.../asset-renderer" && ./gradlew compileJava -q 2>&1 \
  | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS[0]}"
```

Every part drifts: the `cd` prefix, the noise filter (**8 spellings**), `tail -N`
(8/12/15/20/25/30/40), and the `PIPESTATUS` echo. discord4j-framework uses a
parallel `--console=plain ... ===TALLY=== | grep -hoE tests= | awk` variant of
the same shape.

**Recommendation.** One committed wrapper `gw <alias> <task...>` that cd's by
alias, runs gradle with `--console=plain`, applies ONE canonical noise filter,
prints one status line on success or errors-only + real `EXIT <code>` on failure
(temp-log capture, no `PIPESTATUS` guessing), and auto-appends the test tally for
test tasks. Validated live: `gw ar compileJava` ->
`BUILD SUCCESSFUL in 715ms (ar: compileJava) EXIT 0`; the failure path prints
errors-only + `EXIT 1`.

**Deliverable.** `DRAFT-gw.sh` -> `~/.claude/bin/gw` (`chmod +x`), needs
`modules.sh` + `jtally.py` beside it and `~/.claude/bin` on `PATH`.

**Saving.** Per gradle call: ~50 authoring tokens -> ~4 (`gw ar test`), plus
~300-500 read-back tokens (`tail -40` ~600 tok) -> ~20 (one line). Over ~1,500
boilerplate-carrying calls: **~0.5M-0.9M tokens**, ~80% of the command waste.
Forward-looking ~20x per verify cycle in asset-renderer-heavy sessions.

### 5.2 The per-command `cd` prefix (~5,900 occurrences)

**Finding.** ~5,913 commands re-type the long absolute
`cd "W:/Workspace/Java/Simplified/.../module" &&` prefix although the Bash tool's
cwd already persists between calls in a normal session.

**Recommendation.** Two facts, both in the CLAUDE.md hygiene section: (a) cwd
persists - `cd` into the module *once* as a bare command (never `cd X && cmd`,
which can trip the permission prompt), then issue bare `git`/`gradlew`; (b) inside
Agent/subagent threads cwd *does* reset between calls, so there use the
alias-based `gw`/`jtally` helpers (which resolve the dir internally) rather than
relying on cwd. `modules.sh` is the single alias->abs-dir source of truth.

**Deliverable.** `DRAFT-modules.sh` -> `~/.claude/bin/modules.sh`; the "### Bash
cwd persists" block of `DRAFT-command-CLAUDE-md.md`.

**Saving.** ~106k tokens (5,913 x ~18 tokens).

### 5.3 Inline JUnit-XML tallies (~44+ re-authors)

**Finding.** The `build/test-results/test/*.xml` tally is authored inline **44+
times** (python + awk variants), including a repeated `UnicodeDecodeError`
re-author loop where the same parse bug is re-hit and re-fixed from scratch.

**Recommendation.** One committed `jtally.py` that parses the XML, prints a stable
`classes=/tests=/skipped=/failures=/errors=` line plus the failing `Class::test`
names, and exits 0 (green) / 1 (failures) / 2 (nothing ran) so it doubles as a
gate. Validated live: `jtally ar` parsed 97 classes / 590 tests, exit 0. `gw`
auto-appends it for test tasks.

**Deliverable.** `DRAFT-jtally.py` -> `~/.claude/bin/jtally.py`; alias
`jtally='python3 $HOME/.claude/bin/jtally.py'` in `~/.bashrc`.

**Saving.** ~10-15k tokens (44+ x 200-350) plus elimination of the re-author
loop.

### 5.4 echo banners, and shell grep/head/tail vs the Grep/Read tools

**Finding.** `echo` (4,821x) is mostly `=== marker ===` banners and `EXIT:` lines
the helpers make redundant. Source spelunking uses shell `grep`/`head`/`tail`/`cat`
instead of the Grep/Read tools - a `cat`/`head`/`sed -n` on a `.java` file does
**not** register the read gate, directly causing the 220 "has not been read yet"
Edit failures (§6.3).

**Recommendation.** CLAUDE.md rules: search SOURCE with the **Grep tool**
(ripgrep-backed, file links) reserving shell `grep` for piping another command's
output; view files with the **Read tool** with `offset`/`limit` (satisfies the
Edit gate); stop hand-printing `=== marker ===` / `EXIT:` banners - `gw`/`jtally`
label their own output. Install the full "## Shell command hygiene" section.

**Deliverable.** `DRAFT-command-CLAUDE-md.md` -> paste into
`C:/Users/BrianGraham/.claude/CLAUDE.md` (after `## Control Flow`).

**Saving.** ~50-100k tokens of echo/grep/head plumbing plus fewer failed-Edit
round trips.

## 6. Wrong-assumption countermeasures (lead: the ~1,000-error histogram)

### 6.1 The failure histogram

**~1,043** avoidable failed tool calls, categorized:

| Bucket | Count | Share | Raw error signatures |
|---|---:|---:|---|
| Path-guessing | 646 | ~62% | "No such file or directory" 492, "File does not exist" 120 |
| Edit/Write-before-Read | 223 | ~21% | "has not been read yet" 220 |
| Stale / non-unique `old_string` | 164 | ~16% | "String to replace not found" 152, "Found N matches" 12 |
| Schema-type slips | 22 | ~2% | InputValidationError 22 |

The first three buckets are **97%** of the total and every one has a mechanical,
low-context countermeasure. The primary deliverable is exact CLAUDE.md text
(`DRAFT-assumptions-CLAUDE-md.md`, ~61 lines) plus a `locate-java.sh` helper.

### 6.2 Path-guessing (646, ~62%) - the Project Map

**Finding.** The dominant failure class, driven by **five heterogeneous
source-package roots** (verified on disk): asset-renderer=`lib/minecraft/renderer`,
hypixel/mojang=`api/simplified/*`, persistence/discord4j=`dev/simplified/*`,
bot/data=`dev/sbs/*`, minecraft-text=`lib/minecraft/text`. Compounded by **dead
namespaces** (`dev.sbs.discordapi` now `dev.simplified.discordapi`;
`dev.sbs.minecraftapi` split; old `api/.../persistence` now
`dev.simplified.persistence`), by **backslash Windows paths** pasted into git-bash
that collapse (`W:\Workspace\...` -> `W:WorkspaceJava...` -> No such file), by
`/tmp` scratch misses across sessions, and by running `./gradlew` from the wrong
dir (each module is its OWN standalone Gradle build -> `COMPILE_EXIT=127`).

**Recommendation.** A **Project Map** table (module root -> `src/main/java` pkg
root, plus per-module test/resources/test-XML/gradlew conventions and the dead
namespace list) - the single highest-leverage line-for-line addition in this
report - plus a "Path & Shell Discipline" section stating: forward-slash absolute
paths always in Bash; gradlew runs from the module root; scratch goes in the
session scratchpad; `python3` not `python`, `jq`/`strings` absent; verify a path
(Glob / `ls -d`) before Read when unsure.

**Deliverable.** `DRAFT-assumptions-CLAUDE-md.md` (the "## Project Map" and
"## Path & Shell Discipline" sections) -> `C:/Users/BrianGraham/.claude/CLAUDE.md`.

### 6.3 Edit/Write-before-Read (223, ~21%)

**Finding.** The read gate is under-applied - and IDE MCP tools
(`replace_text_in_file`, `apply_patch`, `rename_refactoring`) bypass it, so a
follow-up harness Edit fails "has not been read yet". Shell `cat`/`head` on a file
also does not satisfy the gate (§5.4).

**Recommendation.** A "Read/Edit Discipline" section: Read a file once at its
CURRENT path before the first Edit/Write this session, then batch edits; `git mv`
/ `rename_refactoring` invalidates the prior Read - re-Read at the new path; route
bulk cross-file renames to IDE `replace_text_in_file`/`apply_patch` (which skip
the gate) deliberately, not accidentally.

**Deliverable.** Same file, "## Read/Edit Discipline" section.

### 6.4 Stale / non-unique old_string (164, ~16%)

**Finding.** Clusters in markdown notes/plans and multi-line javadoc, where indent
and ` - ` vs `--` drift break the exact match. "Found N matches" means the anchor
was not unique.

**Recommendation.** Rules in the same section: `old_string` must be exact current
bytes - on "String to replace not found", Read the region and copy verbatim, never
retry a second guess; on "Found N matches" set `replace_all: true` or extend
`old_string` with a unique neighbor line. Plus a general "verify behavior against
current source, not comments or memory" rule (stale comments and guessed
API/builder signatures are a recurring semantic miss - use `get_symbol_info`).

**Deliverable.** Same file. `corrections.txt` (294 self-corrections) is ~90% grep
re-run noise; only ~3 facts are CLAUDE.md-durable and are folded in here.

### 6.5 The locate-java.sh helper

**Finding.** When the module is unknown or the pkg root is heterogeneous, path
resolution becomes a `find`(fail) -> `find`(fail) -> Glob loop.

**Recommendation.** `locate-java.sh <NameOrFragment> [--test|--res]` resolves a
class/file to its absolute forward-slash path across every module root in ONE
`find`, showing cross-root duplicates with full context. Complements the Project
Map (the table tells you the root; the helper finds the file when you don't know
the module).

**Deliverable.** `DRAFT-locate-java.sh` -> `~/.claude/bin/locate-java.sh`
(`chmod +x`).

**Saving (whole section 6).** Removing the top-3 buckets (~1,033 avoidable failed
calls per comparable session-cohort) at a conservative 2 wasted calls + 1 recovery
turn each is **~2,000+ tool calls** and their tokens avoided, against a ~61-line
CLAUDE.md cost (~700 tokens/session). Net high-positive within a few sessions.

## 7. Cross-cutting extrapolations (memory/context, graph-nav)

### 7.1 Memory hygiene - ADOPT

**Finding.** `MEMORY.md` (18.3 KB) is a good on-demand INDEX with "Read before X"
gates. The problem is `architecture_simplified_data_initiative.md` = **146 KB
(~38-40k tokens) in 164 lines**, with single lines up to **9,594 chars**. Its own
`##` headings show it is ~8 topics glued together, so when the MEMORY.md gate
fires the whole ~40k-token blob loads and you cannot Read just the relevant
heading - the content is not line-decomposable. `MEMORY.md` also carries
done/superseded content (a full "Hibernate Upgrade (DONE)" section, several
"earlier said WRONG" reconciliations) that loads every time.

The CLAUDE.md-vs-memory split is already mostly right; the drift is the *reverse*
of the usual - memory holds ARCHIVAL content that should be compacted out of the
hot path, not CLAUDE.md holding project trivia.

**Recommendation.** (a) Split the 146 KB file along its `##` headings into
`memory/architecture/{status,locked-names,locked-decisions,phase-order,
remaining-work,open-questions}.md`, reflow the >6k-char lines (one
sentence/paragraph per line), and point each MEMORY.md gate at the specific
sub-file. (b) Move "Hibernate Upgrade (DONE)" + the "earlier said WRONG"
reconciliations into `memory/archive/hibernate-migration.md` with a one-line
pointer. (c) Tag each correction `SUPERSEDES: <old claim>` + date so recall (and
mempalace, §3) prefers the current fact.

**Deliverable.** Manual split (no script needed); guided by
`DRAFT-mcp-memory-recommendations.md` §3.

**Saving.** ~35k tokens per conditional load of the architecture file whenever the
gate fires with only one topic needed. Free, immediate, pure win.

### 7.2 Light transcript recall

**Finding.** The re-derivation tax is measurable (3,852 greps, 869 ad-hoc tools,
492 "No such file", 220 "not read yet") - much of it rediscovering paths/commands
a past session already found. "What command did I use" recall is **lexical** -
grep nails it, no embeddings needed.

**Recommendation.** A light on-demand recall that ripgreps the existing session
JSONL transcripts (zero always-on tokens, no new binary, same corpus mempalace
would index). Try this *before* enabling any mempalace semantic recall (§3);
reserve the semantic layer for genuinely fuzzy queries.

**Deliverable.** `DRAFT-recall-transcripts.sh` -> `~/.claude/bin/`, optionally
wrapped in a ~15-line `transcript-recall` skill.

**Saving.** Displaces a share of the 869 re-derivations at ~0 always-on cost.

### 7.3 Graph-nav (code-review-graph) - SKIP rewire / TRIAL impact-only

**Finding.** The premise "the user already has a code-review-graph MCP" is
**unverified** - it is referenced by 3 skill files only, is in **no** live tool
list and **no** config (`~/.claude/data/mcp-config.json`, no workspace
`.mcp.json`). Treat it as a **phantom** until `/mcp` proves otherwise. Meanwhile
IntelliJ MCP (`search_symbol` + `get_symbol_info`) is LIVE, AST-accurate, and
already covers declaration + usage lookups as well as a graph would. A graph only
clearly wins on whole-program impact/flow (`get_impact_radius`, `get_flow`,
`get_architecture_overview`). Also `jdtls-lsp` is enabled in settings but its
`jdtls` binary is missing - dead weight; install or disable it.

**Recommendation.** SKIP rewiring symbol/usage skills to the phantom graph (they
already route to IntelliJ correctly). TRIAL the graph for **impact/flow only**,
and only after `/mcp` confirms it is installed AND indexed over all four family
roots (a one-repo index of a four-root workspace gives partial answers while grep
is trivially whole-tree). Let the existing `refactor-safely`/`debug-issue` skills
drive `get_impact_radius`/`get_flow` if confirmed.

**Deliverable.** Decision guidance in `DRAFT-mcp-memory-recommendations.md` §4;
no code to install until `/mcp` verifies the server.

### 7.4 The Grep -> symbol PreToolUse hook

**Finding.** The dominant lever here is **under-invocation, not backend**:
`search_symbol` used **1x ever** vs **3,852 greps**. The symbol-search /
find-usages skills already route correctly to IntelliJ MCP - they are simply not
invoked; Claude greps reflexively. Repointing the route's destination changes a
route nobody takes.

**Recommendation.** FORCE the route with a `PreToolUse` hook on `Grep` (and Bash
`grep --include=*.java`) that intercepts Java symbol/throw/import/caller patterns
and redirects to the symbol-search skill or the live IntelliJ tool. Configure via
the `update-config` skill (it owns "whenever X do Y" harness automation). This one
hook plausibly moves more tokens than any backend swap. It pairs with the §1.5
Grep-first recipes (which make the fallback correct when the IDE is absent).

**Deliverable.** Author the hook via `update-config`; design in
`DRAFT-mcp-memory-recommendations.md` §4.

**Saving.** A find-usages/blast-radius task today burns a multi-root grep +
3-6 disambiguating re-reads (~4-8k tokens); a routed IntelliJ call is ~0.3-0.8k.
At 5-10 such tasks/session, **~15-40k tokens/session** once routing is enforced -
the biggest single navigation win.

## Prioritized install checklist (highest ROI first)

One-time helper install (do this first):

```bash
mkdir -p ~/.claude/bin
cp DRAFT-modules.sh        ~/.claude/bin/modules.sh
cp DRAFT-jtally.py         ~/.claude/bin/jtally.py
cp DRAFT-gw.sh             ~/.claude/bin/gw               && chmod +x ~/.claude/bin/gw
cp DRAFT-locate-java.sh    ~/.claude/bin/locate-java.sh   && chmod +x ~/.claude/bin/locate-java.sh
cp DRAFT-reorder_imports.py ~/.claude/bin/reorder_imports.py && chmod +x ~/.claude/bin/reorder_imports.py
cp DRAFT-recall-transcripts.sh ~/.claude/bin/             && chmod +x ~/.claude/bin/recall-transcripts.sh
# ~/.bashrc:  export PATH="$HOME/.claude/bin:$PATH"
#             alias jtally='python3 $HOME/.claude/bin/jtally.py'
```

Ranked by ROI. "Install at" is the destination path; source is the scratchpad
`DRAFT-*` file.

| # | Item | Source DRAFT | Install at | Rough saving |
|---|---|---|---|---|
| 1 | **Project Map + Path/Read-Edit discipline** (kills 62% of failures) | `DRAFT-assumptions-CLAUDE-md.md` | append 3 `##` sections to `C:/Users/BrianGraham/.claude/CLAUDE.md` | ~2,000+ failed calls avoided |
| 2 | **`gw` gradle wrapper** + `modules.sh` + `jtally.py` (the ~1,500x shape) | `DRAFT-gw.sh`, `DRAFT-modules.sh`, `DRAFT-jtally.py` | `~/.claude/bin/` + PATH | ~0.5M-0.9M tokens |
| 3 | **Shell command hygiene** CLAUDE.md section (cwd, gw, jtally, Grep/Read) | `DRAFT-command-CLAUDE-md.md` | append `## Shell command hygiene` to global CLAUDE.md | ~150-200k tokens |
| 4 | **gradle-verify-gate runner** (240 hand-authored gates) | `DRAFT-gwverify.sh` + patches §1 | `~/.claude/skills/gradle-verify-gate/gwverify.sh` + SKILL.md | ~200-400 tok/invocation |
| 5 | **Grep -> symbol PreToolUse hook** (search_symbol 1x vs 3,852 greps) | design in `DRAFT-mcp-memory-recommendations.md` §4 | `settings.json` via `update-config` skill | ~15-40k tok/session |
| 6 | **Memory hygiene split** (146 KB / 40k-token file) | guidance `DRAFT-mcp-memory-recommendations.md` §3 | `memory/architecture/` + `memory/archive/` | ~35k per gated load |
| 7 | **java-file-mover skill** + **reorder_imports.py** + import-order.md | `DRAFT-java-file-mover-SKILL.md`, `DRAFT-reorder_imports.py`, `DRAFT-import-order.md` | `~/.claude/skills/java-file-mover/`, `~/.claude/bin/` | ~10-30k tokens/move |
| 8 | **java-bulk-rename** own moves + no-IDE recipe | `DRAFT-skill-patches.md` §4 | `~/.claude/skills/java-bulk-rename/SKILL.md` | large share of 1,037 hand-`sed` |
| 9 | **java-symbol-search / find-usages** Grep-first inversion | `DRAFT-skill-patches.md` §5 | both SKILL.md files | post-proc of ~890 greps |
| 10 | **java-exception-class-gen** `final` on children | `DRAFT-skill-patches.md` §2 | that SKILL.md | ~1-3k tok/child |
| 11 | **javadoc-normalize** `_inject_imports` fix + reorder deferral | `DRAFT-skill-patches.md` §3 | `normalize.py` + SKILL.md | correctness + sortimports.py retirement |
| 12 | **locate-java.sh** helper | `DRAFT-locate-java.sh` | `~/.claude/bin/` | shrinks find-fail loops |
| 13 | **Light transcript recall** | `DRAFT-recall-transcripts.sh` | `~/.claude/bin/` | displaces re-derivations |
| 14 | **context-engineering + pattern-recognition** trim/repoint | `DRAFT-skill-patches.md` §9 | both SKILL.md files | ~250 dead lines/session |
| 15 | **Lower-value skill fixes** (import-audit, modifier-audit, record-audit, jmh, dead loose files) | `DRAFT-skill-patches.md` §6-11 | respective skill dirs | opportunistic |
| 16 | **Mempalace save-hooks only** (defer 36-tool recall) | `DRAFT-mcp-memory-recommendations.md` §2 | mempalace plugin, hooks only | capture at ~0/turn |
| 17 | **`simplified-tools` project MCP** (optional veneer) | `DRAFT-simplified-tools-mcp-server.py` + `.mcp.json` | `W:/Workspace/Java/Simplified/` | neutral; trial later |

Items 1-4 are the core: they alone capture the large majority of the recoverable
tokens and should install together. 5-7 are the next tier. 8-15 are amortized
cleanup. 16-17 are trials, gated on their own verification.

## Honest uncertainties

- **Token estimates are directional, not measured.** They are derived from
  command-shape counts x per-shape token deltas, not A/B runs. The ~0.9M-1.1M
  command-waste figure and the 20-40% forward-looking reduction are
  order-of-magnitude. Treat the *ranking* as more reliable than the absolute
  numbers.
- **`gw`/`jtally`/`gwverify` validated on asset-renderer only.** They ran clean
  there; the alias table and noise filter should be spot-checked on a
  discord4j-framework and a persistence build before blanket reliance (d4j uses a
  slightly different tally shape).
- **The PreToolUse routing hook is a design, not a tested artifact.** It must not
  over-fire on non-symbol greps (log strings, comments); tune the intercept
  regex and confirm it degrades gracefully when the IDE is detached.
- **`code-review-graph` and mempalace are both unverified installs.** The graph is
  a phantom until `/mcp` says otherwise; mempalace's binary is unaudited and reads
  transcripts containing Tier-3 secret echoes. Both are gated TRIALS, not adopts.
- **The 146 KB memory split is manual and lossy if rushed** - reflowing 9,594-char
  lines risks dropping a locked decision. Split heading-by-heading with a
  before/after `grep -c` on the locked-decision markers.
- **CLAUDE.md additions cost tokens every session** (~700 for the assumptions
  block, plus the hygiene section). They pay back within a few sessions on the
  measured failure/command counts, but they are a standing cost - keep them dense
  and prune anything that stops earning.

