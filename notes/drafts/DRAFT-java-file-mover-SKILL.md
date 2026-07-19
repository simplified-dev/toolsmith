---
name: java-file-mover
description: Move, relocate, or rename a Java file across packages, directories, or modules with imports, package statement, git history, and dependent references all updated. Auto-invoked when the user says "move X to package Y", "relocate X", "put X in module Z", "move this class into <pkg>", "rename class X" (dir-changing), or before Claude hand-runs `git mv`/`mv` on a `.java` file. Routes type-aware work to IntelliJ `mcp__IntelliJ_IDE__rename_refactoring`; falls back to a deterministic git-mv + package-rewrite + import-fix + reorder pipeline when the IDE is not attached. Delegates symbol discovery to `java-symbol-search`/`java-find-usages`, import reorder to the bundled `reorder_imports.py`, javadoc/FQN fixes to `javadoc-normalize`, and the compile gate to `gradle-verify-gate`.
auto_invoke: true
tags: [java, move, relocate, rename, refactor, git-mv, imports, mcp, intellij]
---

# java-file-mover

Relocating a `.java` file is never a one-step `mv`. The class carries a
`package` statement, an entry in every importer, a git-history thread, and -
when it crosses a module - a Gradle dependency edge. This skill is the single
entry point that covers ~100% of "move / relocate / rename this class" cases,
routing the type-aware parts to IntelliJ and falling back to a deterministic,
IDE-independent pipeline that reproduces the same result.

## When to invoke

- User says "move `X` to package `Y`", "relocate `X`", "put `X` in module `Z`",
  "move this class into `<pkg>`", "extract `X` to `<module>`".
- User says "rename class `X`" - a class rename is a file move (the `.java`
  filename must track the type name), so it enters this skill even when the
  directory is unchanged (Case A).
- You are about to hand-run `git mv Foo.java bar/Foo.java`, `mv` a `.java`
  file, or delete-and-recreate a class in a new location.
- A `java-symbol-search` / `java-find-usages` pass shows a type whose callers
  you now need to re-point after moving it.

Skip when: the "move" is a pure text rename of a non-type token (a log string,
a comment) - that is `java-bulk-rename` with `sed`, not a file move.

## Golden rule: prefer IntelliJ, it does the whole move atomically

If the IDE is attached, `mcp__IntelliJ_IDE__rename_refactoring` (for the
package segment) or IntelliJ's Move refactoring performs the ENTIRE move -
package statement, directory relocation, and every importer - in one type-aware
pass, then you only run the reorder/reformat/verify tail. Everything below in
Cases A-C is the fallback pipeline for when the IDE is NOT attached, or a
verification checklist for what the IDE should have done. Always try the IDE
first; drop to the manual pipeline only on a confirmed miss.

Note the Read-before-Edit interaction (same as `java-bulk-rename`): an IDE move
operates through the IDE, not the harness `Edit` tool, so it does not satisfy
the CLAUDE.md "Read at current path before Edit" gate for follow-up edits. After
any IDE move, **re-`Read` every file you then intend to `Edit`** - the move
invalidated your prior reads and (for the moved file) changed its path.

## Repo reality on this machine (read before any git step)

`W:/Workspace/Java/Simplified` is NOT a git repo. **Each module is its own git
repo** - `Simplified-Dev/persistence/.git`, `Minecraft-Library/asset-renderer/.git`,
`Simplified-Api/hypixel/.git`, etc. (`SkyBlock-Simplified/` is additionally a
repo of its own alongside its module repos.) Consequences that drive the whole
decision tree:

- `git mv` only works **within one module repo**. It cannot move a file from
  `persistence` to `discord4j-framework` - those are two separate `.git`s.
- "the specific repo the file lives in" means the **module** repo. Always
  resolve it by walking up from the file to the nearest `.git`, and run git
  commands with that module dir as cwd (use absolute paths; the Bash tool
  resets cwd between calls, so `git -C <module> ...`).
- Modules are wired by Gradle composite builds (`includeBuild`). A cross-module
  move therefore also needs a `build.gradle(.kts)` dependency edge in the
  destination direction (Case C).

Resolve the owning repo first:

```bash
mod="$(git -C "$(dirname "$FILE")" rev-parse --show-toplevel)"   # module repo root
git -C "$mod" status --porcelain -- "$FILE"                       # is the file tracked & is the tree clean?
```

## Decision tree

```
Is the target directory the same as the source?
├─ YES → the type name is changing, not the location.
│        → Case A (rename in place). Really a java-bulk-rename job.
└─ NO → the file changes directory (package).
        Does the destination resolve to the SAME module repo as the source?
        (same `git rev-parse --show-toplevel`)
        ├─ YES → Case B (intra-module move). git mv keeps history in one repo.
        └─ NO  → Case C (cross-module move). git mv CANNOT span repos;
                 remove in source repo, add in dest repo, wire the gradle edge.

IDE attached at any node? → let IntelliJ Move do it, then run the verify tail.
```

Sub-question that decides the git verb inside Case B/C - is the file tracked
and is its subtree clean enough that `git mv` is safe?

| `git status` of the file | Action |
|---|---|
| tracked, no local edits (or edits you own and mean to keep) | `git mv` - preserves history |
| tracked, but mid-conflict / dirty tree you do not own | stash or ask first; do not `git mv` over someone's WIP |
| untracked (never committed) | plain `mv` (Case B) or `mv` across dirs (Case C); nothing to preserve |
| generated / build output | do not move; it regenerates |

Delete-and-recreate (Read old, Write new, `rm` old) is the **last resort** -
only when the file is not git-tracked and no `mv` is possible. It loses history;
never use it on a tracked file.

## Case A - rename in place (same directory)

The class name changes; the package does not. The `.java` filename must follow
the type name. This is exactly `java-bulk-rename`'s territory:

1. `mcp__IntelliJ_IDE__rename_refactoring` on the type - it renames the
   declaration, every usage, every import, and the file on disk atomically.
2. IDE absent → fall back per `java-bulk-rename`: `java-symbol-search` to find
   references, scoped `Edit` loop (not blind `sed`) for the identifier, then
   `git mv Old.java New.java` inside the module repo for the file itself.
3. Run the verify tail (reorder/reformat/gradle-verify-gate).

Do not duplicate `java-bulk-rename` logic here - invoke it. This skill owns the
directory-changing cases (B and C); Case A is listed only so a "rename class"
request lands somewhere and gets routed correctly.

## Case B - move within the SAME module (same git repo)

Example: `dev.simplified.persistence.source.JsonSource` →
`dev.simplified.persistence.io.JsonSource`, both inside the `persistence` repo.

**IDE path (preferred):** IntelliJ Move Class to the target package. It rewrites
the `package` statement, moves the file, and updates all importers in one pass.
Then jump to the verify tail. Everything below is the IDE-absent fallback.

**Manual pipeline (IDE absent):**

1. Resolve the module repo and confirm same-repo destination:
   ```bash
   mod="$(git -C "$(dirname "$SRC")" rev-parse --show-toplevel)"
   # DST must live under the same $mod. If not, this is Case C.
   ```
2. Discover importers BEFORE touching anything, so you know the blast radius.
   Prefer `java-find-usages` / `java-symbol-search` (AST-aware). IDE-absent
   fallback grep, scoped to the module's sources:
   ```bash
   OLD="dev.simplified.persistence.source.JsonSource"
   grep -rl -e "import ${OLD};" \
            -e "import dev.simplified.persistence.source.\*;" \
            "$mod/src"
   ```
3. Move the file, preserving history and the read-before-edit contract:
   ```bash
   mkdir -p "$(dirname "$DST")"
   git -C "$mod" mv "$SRC" "$DST"        # tracked+clean; else plain: mv "$SRC" "$DST"
   ```
   `git mv` invalidates any prior `Read` of `$SRC` (CLAUDE.md rule) - **re-Read
   at `$DST`** before editing it.
4. Rewrite the moved file's own `package` statement to the new package. Re-Read
   `$DST`, then `Edit` the single `package ...;` line.
5. Update every importer found in step 2 - see "Import-rewrite mechanics".
6. Verify tail.

Because the move stays in one repo, `git mv` records a rename and history is
preserved. Do not delete-and-recreate a tracked file.

## Case C - move to a DIFFERENT module (crosses a git repo)

Example: move a helper from `Simplified-Dev/utils` into
`Simplified-Dev/collections`. Source and destination are **separate `.git`
repos**, so `git mv` is impossible - it errors with "not under version
control" for the destination. This is the case people get wrong.

**IDE path (preferred):** IntelliJ Move Class still works across modules if both
are open in the project; it also offers to add the missing module dependency.
Confirm afterward that the gradle dependency edge (step 5) actually landed -
the IDE edits `.iml`/project model, not always the gradle script.

**Manual pipeline:**

1. Resolve both module repos; confirm they differ:
   ```bash
   srcmod="$(git -C "$(dirname "$SRC")" rev-parse --show-toplevel)"
   dstmod="$(git -C "$DST_DIR" rev-parse --show-toplevel)"   # $srcmod != $dstmod
   ```
2. Move the bytes across the repo boundary in two git ops (history does not
   follow across repos; that is expected and unavoidable here):
   ```bash
   mkdir -p "$(dirname "$DST")"
   cp "$SRC" "$DST"                     # or: mv, then handle the removal below
   git -C "$srcmod" rm "$SRC"           # stage the removal in the source repo
   git -C "$dstmod" add "$DST"          # stage the addition in the dest repo
   ```
   `git rm` refuses a file that has local modifications; since `cp` above left the
   working copy in place, use `git -C "$srcmod" rm -f "$SRC"` (safe - the bytes are
   already in `$DST`), or `git stash` first if the edits are someone else's WIP.
   Re-Read `$DST` before editing (new path).
3. Rewrite the moved file's `package` statement to the destination module's
   package root.
4. **Check dependency DIRECTION before wiring anything.** The type may now
   reference things only visible in the old module, and importers in the old
   module now need the new one. A move is only legal if the destination module
   may depend on what the class uses, and if making importers depend on the
   destination module does not create a cycle:
   ```bash
   # what does the moved class import from its OLD module? those become new deps
   # of the DESTINATION module (or must move too):
   grep -E '^import ' "$DST"
   # who imported it from the OLD module? they now need a dep on the DEST module:
   grep -rl "import ${OLD};" "$srcmod/src"
   ```
   If wiring the edge would invert an existing dependency (dest already depends
   on source), STOP and surface the cycle - the move needs a design decision,
   not a mechanical edit.
5. Add the gradle dependency edge in the destination direction. Re-Read the
   build script first (CLAUDE.md read-before-edit), then add the
   `implementation`/`api`/`includeBuild` line to each importer module's
   `build.gradle.kts` (or `build.gradle`). Match the existing dependency style
   in that file (this codebase uses composite `includeBuild` substitutions).
6. Update every importer across BOTH repos - see "Import-rewrite mechanics".
7. Verify tail, scoped to a **root build** (cross-module change).

## Import-rewrite mechanics (the four edits people forget)

Let `OLD = old.pkg.Type`, `NEW = new.pkg.Type`, `OLDPKG = old.pkg`,
`NEWPKG = new.pkg`. When the IDE does the move it handles all four; when you are
in the manual pipeline you must do each one. `java-find-usages` /
`java-symbol-search` is the right discovery tool - the greps below are the
IDE-absent fallback.

1. **Explicit FQ imports** - the common case. Every file with `import OLD;`
   flips to `import NEW;`.
   ```bash
   grep -rl "import ${OLD};" "$scope/src"   # list importers
   # small blast radius: Edit each (import OLD; -> import NEW;).
   # large blast radius: CLAUDE.md prefers sed over an Edit loop for a bulk text swap -
   #   grep -rl "import ${OLD};" "$scope/src" | xargs sed -i "s#import ${OLD};#import ${NEW};#"
   # (sed needs no prior Read; re-Read any file you then Edit further.)
   ```

2. **Old-package wildcard importers.** A file with `import OLDPKG.*;` that
   references `Type` was resolving it through the wildcard. After the move the
   wildcard no longer covers `Type`, so that file needs `import NEW;` ADDED
   (the wildcard stays for the package's other types). Easy to miss - it has no
   `import OLD;` line to flip.
   ```bash
   grep -rl "import ${OLDPKG}\.\*;" "$scope/src"   # inspect each for a Type reference
   ```

3. **Former same-package siblings.** Types that lived in `OLDPKG` referenced
   `Type` with NO import (same-package visibility). After the move they are no
   longer in `Type`'s package, so each sibling that still uses `Type` needs
   `import NEW;` ADDED.
   ```bash
   grep -rl --include=*.java "\bType\b" "$OLDPKG_DIR"   # siblings still in the old dir
   ```

4. **The moved file's OWN imports (the mirror of #3).** The class itself
   referenced its former same-package siblings with no import. Now in `NEWPKG`,
   it needs `import OLDPKG.Sibling;` ADDED for every type it used from its old
   package. Skim the moved file for unqualified simple names that resolved
   against `OLDPKG`, and add an import per sibling. This is the edit most likely
   to be silently wrong - it compiles-fails only, so lean on `gradle-verify-gate`
   to catch stragglers.

For adding imports (cases 2-4) do not hand-place the line in sorted position -
add it anywhere in the import block and let the reorder step below sort it. For
FQN refs that appear in **javadoc** rather than code, route to
`javadoc-normalize --fix` (it auto-imports and simplifies) instead of editing by
hand.

## The reorder + reformat + verify tail (every case ends here)

Every touched file (the moved file and every importer you edited) ends the same
way:

1. **Reorder imports.** IDE attached → `mcp__IntelliJ_IDE__reformat_file` (or
   Optimize Imports) per file - it uses the live project code style, the only
   fully faithful ordering. IDE absent → the bundled reorderer, which reproduces
   the IntelliJ Default layout byte-for-byte (see DRAFT-import-order.md):
   ```bash
   python3 ~/.claude/skills/java-file-mover/reorder_imports.py FILE [FILE...]
   # or gate a whole subtree without writing:
   python3 ~/.claude/skills/java-file-mover/reorder_imports.py --check "$mod/src"
   ```
   It is idempotent, preserves CRLF/LF and the trailing newline, never touches
   the package statement or leading comments, preserves wildcards verbatim, and
   skips (reports, never corrupts) any file with a comment interleaved in the
   import block. Run it on the importers too - added imports (mechanics #2-4)
   land unsorted by design and this sorts them.

2. **Reformat.** Only when the IDE is attached and indentation may have drifted
   (fallback `sed`/manual edits): `mcp__IntelliJ_IDE__reformat_file` per path.
   `reorder_imports.py` already leaves import lines canonical, so skip a
   separate reformat when the IDE is not available.

3. **Verify.** Invoke `gradle-verify-gate`:
   - Case A / Case B (one module) → module-scoped `:module:compileJava :module:test`.
   - Case C (cross-module) → root `./gradlew build`.
   A move's failure mode is almost always a missed importer (mechanics #2-4) or
   a missing gradle edge (Case C step 5); the gate is what surfaces it.

4. **Optional hygiene.** `java-import-audit` on the touched files catches any
   now-unused `import OLD;` left behind and stray wildcards.

## Failure handling and rollback

The move is staged, not committed (CLAUDE.md: commit only when the user asks),
so rollback is cheap. Because a cross-module move spans two repos, rollback is
**per repo**.

- **Compile fails after the move** → it is a missed importer or a missing
  dependency edge, not a reason to revert. Read the first
  `gradle-verify-gate` error, fix that importer (mechanics #1-4) or add the
  edge (Case C step 5), re-run the gate. Only revert if the move itself was
  wrong.
- **Revert an intra-module move (Case B):**
  ```bash
  git -C "$mod" checkout -- .        # unstage the git mv + restore, if nothing else is staged
  # or precisely: git -C "$mod" mv "$DST" "$SRC" && restore the package line
  ```
- **Revert a cross-module move (Case C):** undo in each repo -
  `git -C "$dstmod" restore --staged "$DST" && rm "$DST"` and
  `git -C "$srcmod" restore --staged "$SRC" && git -C "$srcmod" checkout -- "$SRC"`,
  then drop the gradle edge you added.
- **`git mv` refused ("destination exists" / "not under version control")** →
  you are actually in Case C (repo boundary) or the target path collides. Do not
  force; re-resolve which repo the destination belongs to.
- **Never** resolve a failed move by delete-and-recreate of a tracked file - it
  discards history. Recreation is only for untracked files with no `mv` option.

If a step reveals the move needs a design decision (dependency cycle, a type
used by both modules, a split package), STOP and surface it rather than forcing
a mechanical edit.

## Delegation map

This skill orchestrates; it does not re-implement its neighbors.

| Sub-task | Delegate to | Not this skill's job |
|---|---|---|
| Discover importers / callers of the moved type | `java-find-usages`, `java-symbol-search` | AST search |
| Rename the type identifier (Case A) | `java-bulk-rename` | identifier rewrite |
| Sort the import block after edits | bundled `reorder_imports.py` (IDE-absent) or `mcp__IntelliJ_IDE__reformat_file` | ordering algorithm |
| FQN refs in **javadoc** on the moved type | `javadoc-normalize --fix` | javadoc auto-import |
| Leftover unused / wildcard imports | `java-import-audit` | unused detection |
| Compile + test gate | `gradle-verify-gate` | build invocation |

Canonical pipeline (IDE-absent, cross-package move):

`java-find-usages` → (git mv | git rm+add) + package rewrite → import mechanics
#1-4 → `reorder_imports.py` → `gradle-verify-gate` → (user commits).

The bundled `reorder_imports.py` ships in this skill's directory so the fallback
works with no IDE and no network. The canonical, tested copy is
`Simplified-Dev/toolsmith/src/toolsmith/imports.py` (also exposed as the toolsmith
`reorder_imports` MCP tool) - re-copy from there when it changes.

