# A3 - Skill Audit & Upgrade

Audit of all 16 loaded skills + 4 dead loose `.md` files under
`C:/Users/BrianGraham/.claude/skills`. Each skill assessed on: (a) trigger fit
vs corpus, (b) missed edge cases that forced manual sed/grep/Edit fallback,
(c) correctness bugs, (d) overlap/redundancy, (e) half-done CLAUDE.md
conventions. Ranked by future-token-savings impact.

Deliverables: `DRAFT-skill-patches.md` (per-skill patches, main), `DRAFT-gwverify.sh`
(noise-stripping gradle wrapper helper).

## Evidence base (concrete numbers)

- Loaded skills (from session skill listing): cco, context-engineering,
  gradle-verify-gate, java-bulk-rename, java-exception-class-gen,
  java-find-usages, java-import-audit, java-modifier-audit, java-record-audit,
  java-symbol-search, javadoc-normalize, jmh-regression-gate,
  pattern-recognition, planning-methodology, quality-validation,
  research-methodology. = 16.
- NOT loaded (loose `.md`, no containing dir): debug-issue.md,
  explore-codebase.md, review-changes.md, refactor-safely.md. Absent from the
  skill listing => never auto-invoke. All 4 route to a `code-review-graph` MCP.
- Corpus: Bash 6973, Edit 6443, Grep 892, sed 1037, git mv 63,
  rename_refactoring 32, get_file_problems 148, reformat_file 2, search_symbol 1.
- Gradle noise-strip + PIPESTATUS shape: 240 deduped variants in
  bash_commands_dedup.txt (grep `incubating|warning ... tail ... PIPESTATUS`).
- sortimports.py: ad-hoc reorderer written repeatedly; git-diff-driven
  "sort imports across changed .java" workflow (adhoc_scripts.txt L848/853/860;
  bash_commands_dedup L111 x2); file_ops.txt L236-242 = git mv + package fix +
  per-type FQN sed + sortimports.py repeated ~7x.
- Data files referenced by skills that DO NOT EXIST: `~/.claude/data/pattern-index.json`,
  `~/.claude/scripts/calculate-confidence.sh`. `knowledge-core.md` exists only
  at global `~/.claude/knowledge-core.md`; real project memory is
  `~/.claude/projects/W--Workspace-Java-Simplified/memory/MEMORY.md` (never
  referenced by any skill).
- Exception classes in tree: 64 total `class XException extends`, 24 declared
  `final`, 40 not. Leaf children are expected `final` (java-modifier-audit rule).

## Impact ranking (summary)

1. gradle-verify-gate - ships no wrapper; 240 hand-rewritten noise-strip shapes.
2. java-bulk-rename - 32 uses vs 1037 sed; under-triggers; move workflow uncovered.
3. javadoc-normalize - `_inject_imports` misroute bug; no standalone reorder entry.
4. java-exception-class-gen - omits `final` on leaf, forcing a follow-up audit+edit.
5. java-symbol-search / java-find-usages - route to search_symbol (used 1x); weak fallback.
6. context-engineering + pattern-recognition - dead file refs, generic auto-invoke, emoji.
7. 4 dead loose files - never load; delete or promote to dirs.
8. Lower: java-import-audit, java-modifier-audit, java-record-audit, jmh-regression-gate,
   cco, planning/quality/research methodology.

## Per-skill findings

### 1. gradle-verify-gate (HIGH)

- (a) Trigger fit: GOOD in principle - fires on "after refactor / rename /
  Edit batch / Phase N -> verify". Matches the workload.
- (b) Missed feature - THE gap: the skill documents the *command*
  `./gradlew :module:compileJava :module:test` but ships NO reusable helper
  that strips gradle noise and reports PIPESTATUS. Every run was hand-authored.
  240 deduped variants of `... -q 2>&1 | grep -vE "incubating|warning" | tail -N
  ; echo EXIT ${PIPESTATUS[0]}` prove the wrapper was needed and never existed.
  The tail depth (8/12/15/20/25/30/40) and the noise filter were re-typed each
  time. Many variants also mistakenly used `|` where `&&` was meant (see
  bash_commands_dedup L167/L561: `cd ... | sed ...` pipes cd into sed).
- (c) Correctness: the skill's own "Skip when" heuristics ("last gate run
  timestamp newer than most recent Edit") are unenforceable by the model with
  no state file - pure prose. Fine as guidance, but it claims to "skip
  redundant re-runs when nothing has changed" (description) which it cannot do.
- (d) Overlap: clean. jmh-regression-gate is the sibling.
- (e) CLAUDE.md: does not encode the module roots (asset-renderer etc.) so the
  model still hand-types the `cd "W:/.../asset-renderer"` prefix each run.
- FIX (coordinate with A1, who owns the wrapper): ship `gwverify.sh` that takes
  a module + task list, applies the canonical noise filter, prints only the
  first N error lines, and echoes a stable `GATE: PASS|FAIL rc=<pipestatus>`
  trailer. Wire the skill to call it. Draft: `DRAFT-gwverify.sh`.

### 2. java-bulk-rename (HIGH)

- (a) Trigger fit: UNDER-fires. Used 32x; `sed -i` ran 1037x, `git mv` 63x.
  The skill's trigger is "about to run find -exec sed -i" OR ">1 Edit on
  .java". But the real recurring shape is a *move*: `git mv` + fix `package`
  decl + per-type FQN `sed` across src + `sortimports.py` (file_ops.txt
  L236-242, ~7 repeats). None of that trips the current trigger because the
  operator reaches for `git mv` first (memory says "cd root, git mv") and the
  rename skill never claims the move space.
- (b) Missed edge cases forcing manual sed: import-line swaps after a rename
  (bash_commands_dedup L561/L569: `sed -i 's|^import ...Biome;|import ...Block;|'`)
  - exactly what a type-aware rename would have done atomically. The skill's
  IDE path (`rename_refactoring`, 32 uses) is the minority; the sed fallback is
  the majority, but the skill treats sed as a degraded afterthought with no
  recipe for the common "rename a type across a module without the IDE" case.
- (c) Correctness: the "After running -> reformat_file each file" step is
  unrealistic - reformat_file was used 2x total in the whole corpus. The skill
  over-indexes on an IDE round-trip that does not happen.
- (d) Overlap: with A4's forthcoming file-mover skill (move = rename's sibling).
  Should cross-reference, not duplicate.
- (e) CLAUDE.md: the git-mv-preserves-history rule (MEMORY.md) and the
  "re-Read after git mv invalidates reads" rule are relevant but only half
  referenced.
- FIX: broaden triggers to include package/class MOVES; add a concrete no-IDE
  rename recipe (rg to enumerate -> sed for the identifier -> fix imports ->
  reorder via A4 reorder_imports.py -> gate); cross-ref A4 file-mover; drop the
  blanket reformat_file step, make it IDE-conditional. Patch in DRAFT-skill-patches.md.

### 3. javadoc-normalize (HIGH)

- (a) Trigger fit: GOOD. Real python tool, exercised heavily; solid.
- (c) Correctness BUG in `_inject_imports._route` (normalize.py L329-368):
  `_top_prefix()` strips a leading `static ` and returns the package segment,
  so a static import contributes a prefix. Consequence: when FQN-auto-import
  needs to ADD a plain `java.util.X` import and the file has NO plain java
  group but HAS a static group containing e.g. `import static java.lang.Math.max`,
  step-1 prefix matching (`any(_top_prefix(e)=='java' for e in g)`) matches the
  STATIC group and appends the new plain import there; `g.sort()` then places
  it above the static lines. Net: a non-static import lands inside the static
  block - wrong per IntelliJ Default (verified empirically: real files order
  group1(com/dev/lib/org) / blank / java.* / blank / static). Same
  contamination can mis-flag a static-only group as the "java group"
  (L337-341). FIX: exclude static lines from prefix routing + java-group
  detection. Exact diff in DRAFT-skill-patches.md.
- (b) Missing feature - NO standalone reorder entry point. `_inject_imports`
  only runs as a side effect of adding an FQN import. The recurring need is
  "reorder EXISTING imports of changed files to IntelliJ order" (sortimports.py,
  6+ repeats, git-diff driven). That ad-hoc tool is wrong (interleaves static,
  never crosses blank groups). A4 owns `DRAFT-reorder_imports.py` +
  `DRAFT-import-order.md` (the empirical order). javadoc-normalize should
  DEFER to it, not grow a second reorderer. SKILL patch: add a "Reordering
  imports" section pointing at A4's tool and stating normalize only *inserts*.
- (d) Overlap: java-import-audit already correctly defers FQN handling here. OK.
- (e) CLAUDE.md: package-info.java exception handled correctly; java/javax
  grouping should be validated against A4's import-order.md (alphabetical
  `g.sort()` yields java before javax; confirm that matches the IDE - no
  file in-tree has both java and javax to prove it locally).

### 4. java-exception-class-gen (HIGH)

- (a) Trigger fit: GOOD.
- Template correctness vs CLAUDE.md `## Exceptions`: VERIFIED correct.
  Constructor order (5), root reverses `super(message, cause)` /
  `super(String.format(message, args), cause)`, child passes through
  `super(cause, message)` / `super(cause, message, args)`, `@NotNull` on
  cause/message, `@PrintFormat`+`@Nullable` on the vararg ctors, javadoc
  "Thrown when..." / "Constructs a new {@code X} with..." - all match.
- (e) Half-done convention - THE gap: the template emits
  `public class FooException` / `public class FooBarException` with NO `final`.
  But leaf/child exceptions are expected `final` (java-modifier-audit rule +
  MEMORY). Corpus: 64 XException classes, 24 already `final`. java-modifier-audit
  even documents this as a known miss it has to catch. So the generator forces a
  guaranteed follow-up (modifier-audit -> Read -> Edit -> re-gate) on every
  child it produces. FIX: CHILD template emits `public final class` (leaf);
  ROOT template stays non-`final` (roots are extended by children -
  `final` root would be a compile error for the child template). This is the
  precise, defensible split. Full replacement in DRAFT-skill-patches.md.
- (b) Minor: no template for an *abstract* root (a root that must be extended
  but never instantiated). Optional add; low value.
- (d) Overlap: clean handoff to javadoc-normalize (correctly says "do NOT
  re-run normalize on the generated file").

### 5. java-symbol-search + java-find-usages (MEDIUM-HIGH)

- (a) Trigger fit: BROKEN in practice. Both route primarily to
  `mcp__IntelliJ_IDE__search_symbol` / `get_symbol_info`. `search_symbol`
  appears ONCE in the entire corpus; Grep ran 892x. The "IDE is attached"
  assumption does not hold for these two tools in this operator's real
  sessions (contrast get_file_problems, used 148x - so SOME IDE tools do fire;
  search_symbol/get_symbol_info specifically do not). The skills present Grep
  as a degraded fallback footnote, but Grep is the actual primary path.
- (b) The Grep fallbacks are thin: they say "glob **/*.java, -n, -C 2" but give
  no ready-to-run recipes for the high-frequency queries (throw sites,
  subclasses, import-of-FQN, callers). The operator hand-built these 892 times.
- (c) Correctness: java-find-usages claims get_symbol_info "distinguishes read
  vs write in some IDE versions" - speculative and unactionable.
- (d) Overlap: the split into two skills is defensible (declaration vs usage)
  but both duplicate the same MCP-first / Grep-fallback framing and both
  under-fire for the same reason.
- FIX: invert the framing - Grep/ripgrep FIRST with concrete recipes, IDE as an
  optional precision upgrade "if attached". Ship a copy-paste recipe table
  (throw sites, extends/implements, import-of, simple-name callers) using
  `grep -rn --include=*.java` (rg is barely installed here: rg 34 vs grep 3852,
  so recipes must use grep, not rg). Patch both in DRAFT-skill-patches.md.

### 6. context-engineering + pattern-recognition (MEDIUM)

- (c) Correctness / dead refs: pattern-recognition (~800 lines) drives an
  entire "NEW v3.1" pattern-index/confidence engine against
  `~/.claude/data/pattern-index.json` and `~/.claude/scripts/calculate-confidence.sh`
  - BOTH ABSENT (verified). It degrades gracefully (skips), so the payload is
  ~800 lines of instructions that no-op. context-engineering targets
  `knowledge-core.md`, which exists only globally (`~/.claude/knowledge-core.md`),
  never for this project. The REAL memory substrate the operator uses -
  `~/.claude/projects/W--Workspace-Java-Simplified/memory/MEMORY.md` + dedicated
  files, plus the mempalace plugin noted in context - is referenced by NEITHER
  skill. So both auto-invoke constantly and point at the wrong store.
- (a) Trigger fit: TOO broad. Both are `auto_invoke: true` on generic triggers
  ("conversation exceeds 50 messages", "after tool use", "after successful
  implementations"). They fire every long session and compete for attention -
  the opposite of the token-savings goal.
- (d) Overlap: `context-engineering` skill vs the built-in `context` slash
  command (both "analyze/optimize context, 39%/84%"). Redundant surface.
- (e) CLAUDE.md conflict: both emit emoji-heavy output templates (checkmarks,
  brain, map). User CLAUDE.md + task rules say avoid emojis. The skills instruct
  the model to PRINT emojis.
- FIX (low-code, high-signal): (1) repoint both at the real MEMORY.md path or
  delete the dead-file machinery; (2) narrow auto_invoke to explicit user
  request only (drop the message-count/after-tool auto-fire); (3) strip emoji
  from output templates; (4) note the `context` command overlap. Recommendations
  in DRAFT-skill-patches.md - these are large files; I recommend surgical
  frontmatter+top-section edits rather than full rewrites.

### 7. Dead loose files (MEDIUM)

- debug-issue.md, explore-codebase.md, review-changes.md, refactor-safely.md
  sit directly under `skills/` with no containing directory and frontmatter
  `name: Debug Issue` (Title Case, spaces). They are ABSENT from the loaded
  skill listing => the loader ignores them; they never auto-invoke. Dead weight.
- All 4 route to a `code-review-graph` MCP (`semantic_search_nodes`,
  `query_graph`, `get_flow`, `get_impact_radius`, `refactor_tool`,
  `apply_refactor_tool`, `detect_changes`). If that MCP is live, these are
  genuinely useful (graph-nav beats grep for callers/impact) and the fix is to
  promote each into its own dir with a kebab-case name + a real description so
  it loads. If the MCP is NOT installed, delete them.
- Notably `refactor-safely.md`'s `refactor_tool mode=rename` +
  `apply_refactor_tool` OVERLAPS java-bulk-rename (a second rename engine) and
  `review-changes.md` overlaps the built-in `/code-review`. Consolidation
  needed either way.
- FIX: recommend promote-or-delete decision + kebab-case rename; if promoted,
  cross-link refactor-safely <-> java-bulk-rename so they don't fight.

### 8. java-import-audit (LOW-MEDIUM)

- (a) Fit: OK. (d) Correctly defers FQN-in-javadoc to javadoc-normalize and
  wildcard/unused to IDE inspections. Sound boundaries.
- (b) Missing: the recurring need was import REORDERING (sortimports.py), which
  this skill does not mention at all. It covers wildcard/unused/FQN but not
  "wrong order". FIX: add a fourth row routing "imports in wrong order" to A4's
  reorder_imports.py.
- (c) The IDE-attached unused-import fallback ("no clean fallback") is honest;
  could add `./gradlew compileJava` + `-Xlint` guidance. Minor.

### 9. java-modifier-audit (LOW)

- (a) Fit OK; conservative, audit-only - correct posture.
- (c) References a plan file path `scan-the-codebase-for-composed-tarjan.md
  line 19` as justification - a design-scaffolding ref that violates the
  CLAUDE.md "no design-scaffolding refs" rule and will rot. FIX: replace with
  the standing convention statement ("leaf/child exception classes are final").
- (d) Correctly flags that java-exception-class-gen omits `final`; once the gen
  patch lands (emits final on child), this cross-ref should soften to "verify"
  rather than "catches the miss".

### 10. java-record-audit (LOW)

- (a) Fit OK. (b) Honest TODO: the passing-language heuristic is not mechanical.
  Discovery regex `^public\s+record` MISSES non-public / nested / `final record`
  and records with no modifier. FIX: widen to `\brecord\s+\w+\s*\(`. Small.
- (d) Heavy overlap with javadoc-normalize (it says the mechanical part is
  "already scripted as part of javadoc-normalize"). Fine as a thin routing
  wrapper, but flag that it adds little beyond a regex + a prose table.

### 11. jmh-regression-gate (LOW - one real bug)

- (c) BUG: `main()` compiles `--ignore` patterns into `ignored` but the earlier
  `_parse_text_row`/direction inference is fine; the actual defect is that
  `--require-pairs` computes `only_baseline`/`only_candidate` AFTER `--ignore`
  deletion (good) but `render_text` recomputes direction per call and the
  markdown path recomputes regressions independently - duplicate work, not
  wrong. The genuine gap: `_infer_direction` returns True (higher-better) on an
  EMPTY paired list, so if all benchmarks are "only in one file" the tool exits
  0 silently even with --require-pairs off. Low severity; document that a
  0-pair comparison should warn. Otherwise solid.
- (a) Fit OK; niche (JMH only appears in concurrent-perf plans).

### 12. cco / planning / quality / research methodology (LOW)

- cco: trivial launcher; correct. No change.
- planning/quality/research methodology: generic BRAHMA-workflow skills,
  `auto_invoke: true`, heavy emoji output templates (map, chart, check),
  Node/Python-centric examples (npm, package.json) not Java/Gradle. They fire
  broadly. (e) Emoji output conflicts with CLAUDE.md. (a) Over-broad auto-invoke.
  FIX (low priority): strip emoji from emitted templates; add a Java/Gradle
  example alongside the npm one; consider gating auto_invoke to the /research,
  /plan, /workflow commands that already exist rather than free-firing.

## Cross-agent coordination

- A1 owns the gradle noise-strip wrapper. My DRAFT-gwverify.sh is a
  ready-to-install reference; if A1 ships one, gradle-verify-gate should call
  A1's and mine is redundant. The SKILL patch references `gwverify.sh` by name
  so either implementation satisfies it.
- A4 owns `DRAFT-reorder_imports.py`, `DRAFT-import-order.md`,
  `DRAFT-java-file-mover-SKILL.md`. My patches DEFER to these: javadoc-normalize
  and java-import-audit route reordering to A4's tool (I do NOT add a second
  reorderer); java-bulk-rename cross-links A4's file-mover for the move space.
  I only FIX the existing `_inject_imports` misroute bug so normalize's own
  insertion stays IntelliJ-correct.
- A4 must confirm java-before-javax ordering in import-order.md; normalize's
  alphabetical within-group sort assumes it.

## Deliverables index

- `DRAFT-skill-patches.md` - per-skill patches (main deliverable): full SKILL.md
  replacements for gradle-verify-gate and java-exception-class-gen; exact
  normalize.py code diff; diff-style edits for java-bulk-rename,
  java-symbol-search, java-find-usages, java-import-audit, java-modifier-audit,
  java-record-audit, context-engineering, pattern-recognition, dead files.
- `DRAFT-gwverify.sh` - runnable noise-stripping gradle gate helper (A1 coord).
