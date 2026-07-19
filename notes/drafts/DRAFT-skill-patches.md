# DRAFT-skill-patches.md

Per-skill patches to close the gaps found in the A3 audit, ranked by
future-token-savings impact. Each section is ready to install: full SKILL.md
replacements where the change is structural, exact diffs where surgical.

Conventions in drafted Java/javadoc follow the user CLAUDE.md (single-hyphen
javadoc, exception constructor order, brace-omission on single-line bodies,
git mv for moves).

Coordination: A1 owns the gradle wrapper (see `DRAFT-gwverify.sh`); A4 owns
`reorder_imports.py`, `import-order.md`, and the file-mover skill. Patches here
DEFER to those rather than duplicating them.

Install order suggestion: 1 -> 4 first (highest hit rate), then 2/3/5.

---

## 1. gradle-verify-gate - add the missing wrapper (HIGH)

Problem: the skill names the command but ships no reusable, noise-stripping
runner, so the gate was hand-authored 240 times with drifting `tail -N` and
filter fragments, and buggy `|` vs `&&` chaining.

Fix: install `DRAFT-gwverify.sh` to `~/.claude/skills/gradle-verify-gate/gwverify.sh`
(`chmod +x`) and add the section below to the SKILL.md, replacing the
"## Standard invocation" block.

```markdown
## Standard invocation

Use the bundled wrapper - it captures the true gradle exit code (never the
`${PIPESTATUS}`-through-a-pipe trap), strips the standard noise, prints only
the first N signal lines, and ends with a greppable `GATE: PASS|FAIL rc=<n>`
trailer:

    ~/.claude/skills/gradle-verify-gate/gwverify.sh -d "<module-dir>" [-m <module>] [TASK ...]

Examples:

    # single module, compile + test (the default tasks)
    ~/.claude/skills/gradle-verify-gate/gwverify.sh -d "W:/Workspace/Java/Simplified/Minecraft-Library/asset-renderer"

    # compile-only sanity check
    ~/.claude/skills/gradle-verify-gate/gwverify.sh -c -d "$PWD"

    # module-scoped from the family root
    ~/.claude/skills/gradle-verify-gate/gwverify.sh -m asset-renderer :asset-renderer:compileTestJava

Do NOT re-type `./gradlew ... 2>&1 | grep -vE ... | tail -N ; echo ${PIPESTATUS[0]}`
by hand. That shape drifts every run and loses the exit code through the grep
pipe. The wrapper is the single source of the noise filter and the verdict.

If A1 has installed a repo/global gradle wrapper of the same shape, call that
instead - this skill only requires SOMETHING that emits the `GATE:` trailer.
```

Also add the module roots to the decision table so the `-d` value is not
guessed each time:

```markdown
## Module roots (this workspace)

| Family root | modules |
|---|---|
| SkyBlock-Simplified | simplified-bot, simplified-server |
| Simplified-Dev | persistence, discord4j-framework, spring-framework, annotations, collections, gson-extras, image |
| Simplified-Api | mojang, hypixel |
| Minecraft-Library | asset-renderer, minecraft-text, nbt-factory, vanilla-reference-harness |

Pass the module's own dir to `-d`; each family root and each module carries its
own `gradlew` (the wrapper walks up to find it).
```

## 2. java-exception-class-gen - emit `final` on leaf children (HIGH)

Problem: the child template emits `public class FooBarException` (no `final`),
but leaf exceptions are expected `final` (java-modifier-audit rule; 24/64 tree
classes already final). Every generated child forces a follow-up
modifier-audit -> Read -> Edit -> re-gate. The root correctly stays non-final
because the child template extends it.

Fix A (template): in the CHILD template only, change the class declaration.

    - public class FooBarException extends FooException {
    + public final class FooBarException extends FooException {

Leave the ROOT template as `public class FooException extends RuntimeException`
(a `final` root cannot be extended - it would break the child template and any
future child).

Fix B (add a short "## Modifiers" section after "## Message conventions"):

```markdown
## Modifiers

- **Child / leaf exceptions** (extend a project root) are `public final class` -
  they are terminal and not meant to be subclassed. This matches the project
  convention that leaf exception classes are `final`, and pre-empts the
  `java-modifier-audit` flag that would otherwise require a follow-up edit.
- **Root exceptions** (extend `RuntimeException`) stay `public class` (no
  `final`): a root exists to be extended by children. Make a root `abstract`
  instead of `final` if it must never be instantiated directly.
```

Fix C (update "## After running" cross-ref): change the modifier-audit note
from "does NOT add `final`" to reflect that children now ship `final`, so
modifier-audit only *verifies*. Coordinate with patch 7 below.

Verification the drafted template still conforms to CLAUDE.md `## Exceptions`:
constructor order and `super(...)` argument reversal are unchanged by adding
`final`; `final` on the class does not affect constructor bodies. Confirmed OK.

## 3. javadoc-normalize - fix `_inject_imports` misroute + defer reordering (HIGH)

### 3a. Code fix (normalize.py `_inject_imports`, ~L329-368)

Bug: `_top_prefix()` strips a leading `static `, so a `import static java...`
line reports prefix `java`. A new plain `java.util.X` import added by
FQN-auto-import can then match a static-ONLY group and, after `g.sort()`, land
inside the static block - wrong per IntelliJ Default (group1 / java / static).
Fix: add an `_is_static` helper and match new (always non-static) imports only
against non-static existing lines.

```diff
+    def _is_static(line: str) -> bool:
+        return line[len('import '):].startswith('static ')
+
     def _top_prefix(line: str) -> str:
         """Top-level package segment of an import line, e.g. 'java' or 'dev'."""
         body = line[len('import '):]
         if body.startswith('static '):
             body = body[len('static '):]
         return body.split('.', 1)[0]

-    # Locate the "java group" - the group whose imports are all java.*/javax.*.
+    # Locate the "java group": >=1 non-static java/javax import and no non-static
+    # non-java import. A static-only group (which a bare _top_prefix mislabels as
+    # 'java' via `import static java...`) must never be treated as the java group.
     java_idx = -1
     for i, g in enumerate(groups):
-        if g and all(_top_prefix(line) in ('java', 'javax') for line in g):
+        non_static = [l for l in g if not _is_static(l)]
+        if non_static and all(_top_prefix(l) in ('java', 'javax') for l in non_static):
             java_idx = i
             break

     def _route(line: str) -> None:
-        # 1. Prefer a group that already contains an import sharing the same
-        #    top-level package segment (e.g. dev.x -> the dev.* group).
+        # New FQN-auto-import lines are always non-static, so match only against
+        # NON-static existing lines; else a static-only group wrongly attracts
+        # a plain import and g.sort() drops it into the static block.
         prefix = _top_prefix(line)
         for i, g in enumerate(groups):
-            if any(_top_prefix(e) == prefix for e in g):
+            if any((not _is_static(e)) and _top_prefix(e) == prefix for e in g):
                 g.append(line)
                 return
```

Also harden the step-3 non-java fallback so it never drops into a static-only
group:

```diff
         # 3. Otherwise drop into the first non-java group, or create one.
         for i, g in enumerate(groups):
-            if i != java_idx:
+            if i != java_idx and any(not _is_static(e) for e in g):
                 g.append(line)
                 return
         groups.insert(0, [line])
```

Add a regression fixture: a file whose only java import is
`import static java.lang.Math.max;` in a trailing static group, plus a javadoc
`{@link java.util.List}` to auto-import. Expected: a NEW `java.*` group is
created (or the plain import is NOT appended to the static group). The existing
`test_fixture_groups.java` / `test_fixture_imports.java` do not cover this.

### 3b. SKILL.md - defer reordering to A4, state normalize only inserts

Add after "## Verification & invariants":

```markdown
## Reordering existing imports (out of scope)

This skill only INSERTS imports (as a side effect of FQN auto-import) into the
correct existing group. It does NOT reorder an already-written import block.
For "the imports are in the wrong order" - the recurring need previously served
by the ad-hoc `sortimports.py` (which interleaves `import static` with normal
imports and never crosses blank-line groups, so it is WRONG) - use the
dedicated IntelliJ-order reorderer (`reorder_imports.py`, see `import-order.md`
for the empirical order: group1 all-other / blank / java+javax / blank /
static). Do not grow a second reorderer here.
```

## 4. java-bulk-rename - own the move space, real no-IDE recipe (HIGH)

Problem: 32 uses vs 1037 `sed`, 63 `git mv`. The IDE `rename_refactoring` path
is the minority; the real recurring shape is a MOVE (git mv + fix package decl +
per-type FQN sed + reorder imports, ~7 repeats) plus post-rename import-line
fixups done by hand. The skill treats sed as a degraded footnote and over-relies
on `reformat_file` (used 2x total).

### 4a. Broaden triggers (frontmatter description + "## When to invoke")

Add these trigger phrases:
- "move class/package X to Y", "relocate X", "git mv ... .java"
- about to hand-edit a `package ...;` declaration after moving a file
- about to `sed` an `import <fqn>;` line to a new FQN after a rename/move

### 4b. Add "## Moves (package / class relocation)" section

```markdown
## Moves (package / class relocation)

A move is a rename of the fully-qualified name. Route it the same way:

1. IDE attached -> `mcp__IntelliJ_IDE__rename_refactoring` on the package or
   class; it moves the directory, rewrites `package`, and fixes every import
   and usage atomically.
2. IDE not attached -> use A4's `java-file-mover` skill, which scripts:
   `git mv` (preserves history) -> rewrite the `package` line -> rewrite every
   `import <oldfqn>;` across the tree -> reorder imports via `reorder_imports.py`
   -> `gwverify.sh`. Do NOT hand-assemble this from loose `git mv` + `sed`; that
   is exactly the drift this skill exists to prevent.

Per CLAUDE.md: after any `git mv`, prior `Read`s of the moved file are invalid -
re-`Read` at the NEW path before any `Edit`.
```

### 4c. Replace the weak "## Fallback" with a concrete no-IDE rename recipe

```markdown
## Fallback - no-IDE type rename recipe

When `mcp__IntelliJ_IDE__*` errors out (no IDE attached - the common case here),
a type-aware-ISH rename by hand. Flag the degradation in your reply, then:

    MOD="W:/Workspace/Java/Simplified/Minecraft-Library/asset-renderer"
    OLD=OldName ; NEW=NewName
    # 1. enumerate real references (declarations, usages, imports)
    grep -rln --include=*.java "\b${OLD}\b" "$MOD/src"
    # 2. swap the identifier (word-boundary; skips substrings like OldNameX)
    grep -rl --include=*.java "\b${OLD}\b" "$MOD/src" \
      | xargs sed -i "s/\b${OLD}\b/${NEW}/g"
    # 3. rename the declaring file + fix its import lines are handled by the
    #    swap above (import lib.x.OldName -> lib.x.NewName). git mv the file:
    git -C "$MOD" mv src/.../${OLD}.java src/.../${NEW}.java
    # 4. reorder imports on touched files, then gate
    reorder_imports.py $(grep -rl --include=*.java "\b${NEW}\b" "$MOD/src")
    ~/.claude/skills/gradle-verify-gate/gwverify.sh -d "$MOD"

Caveats vs the IDE: `\b${OLD}\b` cannot disambiguate two overloads or a
same-named symbol in another package - eyeball the step-1 list first. Comments
and strings containing the word are also swapped; review the diff.
```

### 4d. Soften the reformat step

Change "## After running" step 1 from "Reformat EVERY file ... call
`reformat_file` on each affected path" to: "If the IDE is attached, reformat
touched files with `reformat_file`; otherwise rely on `reorder_imports.py` +
the fact that `rename_refactoring`/sed preserve layout. `reformat_file` is only
worthwhile when the IDE is already live."

## 5. java-symbol-search + java-find-usages - Grep-first (MEDIUM-HIGH)

Problem: both route primarily to `search_symbol`/`get_symbol_info`, which
appear ~1x in the entire corpus; Grep ran 892x. The "IDE attached" assumption
does not hold for these two MCP tools (unlike `get_file_problems`, 148x). The
Grep fallback is the real primary path but is written as a thin footnote.

Fix: invert the framing to Grep-FIRST with concrete recipes, IDE as an optional
precision upgrade. Note: `rg` is barely present (34 vs grep 3852) - recipes
MUST use `grep`, and the harness Grep tool, not ripgrep.

### 5a. java-symbol-search - replace "## Fallback" and reorder the triage

Move Grep to the top as the default and demote the MCP table to "If the IDE is
attached (verify with a cheap `get_file_problems` first), these give AST-exact
results:". Add a ready-to-run recipe block:

```markdown
## Default: grep recipes (IDE-free)

    SRC=<module>/src
    # throw sites of X
    grep -rnE --include=*.java "throw new ${X}\b" "$SRC"
    # subclasses / implementors of X
    grep -rnE --include=*.java "(extends|implements)[^{]*\b${X}\b" "$SRC"
    # files importing a specific FQN
    grep -rln --include=*.java "^import ${FQN};" "$SRC"
    # every mention of a simple name (usages, over-broad - filter by eye)
    grep -rn --include=*.java "\b${X}\b" "$SRC"
    # enumerate files by name
    grep -rl --include=*.java . "$SRC" | grep -E "${X}\.java$"

Prefer the harness `Grep` tool (integrates with the permission UI and returns
`{file,line}` directly) over shelling out to `grep`. Use `output_mode:content`
with `-n` and `-C 2` when you need surrounding lines.
```

### 5b. java-find-usages - same inversion + drop the speculative claim

- Lead with the grep simple-name recipe; present `get_symbol_info` as "if the
  IDE is attached" only.
- DELETE the unverifiable line "references distinguish read vs write in some IDE
  versions" - it is speculative and unactionable. Replace with: "to separate
  reads from writes, grep for `\bX\s*=` (writes) vs `\bX\b` (all)".
- Keep the honest AST caveats (overloads, inheritance) as the reason to UPGRADE
  to the IDE when it is available, not as the default assumption.

Both skills keep their cross-refs (rename -> java-bulk-rename, gate ->
gradle-verify-gate).

## 6. java-import-audit - route reorder to A4 (LOW-MEDIUM)

The skill audits wildcards, unused, and FQN-in-javadoc but has no row for
imports in the WRONG ORDER - the sortimports.py need. Add a triage row:

```markdown
| Imports out of order (not IntelliJ Default) | `reorder_imports.py` | Rewrites
to group1 all-other / java+javax / static per `import-order.md`. Do NOT use the
old sortimports.py (interleaves static, ignores blank-line groups). |
```

Add to "## After running": "If reordering was applied, `gwverify.sh` still
runs - reordering is pure text but a mis-parsed static import could drop a
symbol out of scope."

## 7. java-modifier-audit - drop design-scaffolding ref (LOW)

- (e) Violates CLAUDE.md "no design-scaffolding refs": it cites
  "`scan-the-codebase-for-composed-tarjan.md` line 19" as the source for the
  final-exception rule. Replace that sentence with the standing convention:
  "Project leaf/child exception classes are `final` (roots stay non-final)."
- Update the java-exception-class-gen cross-ref: now that the generator emits
  `final` on children (patch 2), change "does NOT add `final`; this skill
  catches the miss" to "emits `final` on children; this skill VERIFIES and
  catches any root that should have been a leaf".

## 8. java-record-audit - widen record discovery regex (LOW)

- Discovery regex `^public\s+record\s+\w+` misses non-public, nested,
  `final record`, and no-modifier records. Replace with `\brecord\s+\w+\s*\(`
  (matches the record header form) in both the description example and the
  "## How to use" discovery step.
- The skill is otherwise a thin routing wrapper over javadoc-normalize; that is
  acceptable, but state explicitly at the top: "This skill adds only the
  passing-language heuristic; all mechanical checks run via javadoc-normalize."

## 9. context-engineering + pattern-recognition - repoint memory, de-emoji (MEDIUM)

These are large (~440 and ~800 lines). Surgical edits, not rewrites:

### pattern-recognition
- DEAD REFS: `~/.claude/data/pattern-index.json` and
  `~/.claude/scripts/calculate-confidence.sh` DO NOT EXIST. The entire
  "NEW v3.1" suggestion/confidence engine (Steps 5-7, ~250 lines) no-ops.
  Either (a) delete Steps 5-7 and the "Before Implementation" suggestion block,
  or (b) create the two files. Recommend (a) - the machinery has no backing
  store and adds ~250 lines of never-firing instructions to every session.
- REPOINT: `knowledge-core.md` should be the real project memory,
  `~/.claude/projects/W--Workspace-Java-Simplified/memory/MEMORY.md` (+ dedicated
  files). Add a line at the top: "This project's memory lives at MEMORY.md
  under the project memory dir; write learnings there, not a bare
  knowledge-core.md."
- AUTO-INVOKE: narrow from "after successful implementations / Stop hook" to
  explicit user request, so it stops firing every session.

### context-engineering
- De-emoji the CLAUDE.md-optimization and mid-session example templates
  (checkmarks, brain) - CLAUDE.md says avoid emojis in output.
- Note the overlap with the built-in `context` slash command (same 39%/84%
  claims); pick one entry point. Recommend keeping `context` (command) and
  demoting this skill to a referenced methodology doc with `auto_invoke: false`.
- Repoint `knowledge-core.md` mentions to the real MEMORY.md path as above.

## 10. Dead loose files - promote or delete (MEDIUM)

`debug-issue.md`, `explore-codebase.md`, `review-changes.md`,
`refactor-safely.md` sit directly under `skills/` (no containing dir,
`name: Title Case`) and are ABSENT from the loaded skill listing - they never
auto-invoke. Decision:

- If the `code-review-graph` MCP is installed and wanted: promote each to its
  own dir with a kebab-case name and a trigger-bearing description, e.g.
  `git -C ~/.claude/skills mv debug-issue.md debug-issue/SKILL.md` then set
  `name: debug-issue` and expand `description:` with the "who calls / impact /
  trace bug" trigger phrases. Do the same for the other three.
- Then de-conflict: `refactor-safely`'s `refactor_tool mode=rename` +
  `apply_refactor_tool` is a SECOND rename engine - cross-link it with
  java-bulk-rename ("graph-driven rename when the code-review-graph MCP is
  attached; IDE rename_refactoring otherwise"). `review-changes` overlaps the
  built-in `/code-review` - note which wins.
- If the MCP is NOT installed: delete all four (dead weight, ~60 lines total,
  and their tool names will never resolve).

Recommendation: verify the MCP with a single `semantic_search_nodes` probe; if
it errors, delete; if it answers, promote + de-conflict.

## 11. jmh-regression-gate - warn on zero-pair compare (LOW)

- `_infer_direction([])` returns True and `has_regression` over an empty
  `paired` list is False, so a run where NO benchmarks pair up (renamed
  benchmarks, mismatched params) exits 0 - a misleading green. Add, after the
  `paired, only_baseline, only_candidate = compare(...)` call in `main()`:

```python
    if not paired:
        print("no benchmarks paired across the two files - nothing compared",
              file=sys.stderr)
        return 2
```

- Otherwise the tool is correct and self-contained. No other change.

## Summary table

| # | Skill | Change | Token-savings driver |
|---|---|---|---|
| 1 | gradle-verify-gate | wrapper `gwverify.sh` | 240 hand-authored gate shapes |
| 2 | java-exception-class-gen | `final` on child | removes follow-up audit+edit per class |
| 3 | javadoc-normalize | misroute fix + reorder deferral | correctness + sortimports.py replacement |
| 4 | java-bulk-rename | own moves, no-IDE recipe | 1037 sed vs 32 uses |
| 5 | symbol-search/find-usages | Grep-first | 892 greps, search_symbol used 1x |
| 6 | java-import-audit | route reorder to A4 | wrong-order gap |
| 7 | java-modifier-audit | drop scaffolding ref | CLAUDE.md conformance |
| 8 | java-record-audit | widen regex | missed record forms |
| 9 | context/pattern | repoint memory, de-emoji, trim | dead 250+ lines, wrong store |
| 10 | 4 loose files | promote or delete | never load today |
| 11 | jmh-regression-gate | zero-pair guard | false green |
