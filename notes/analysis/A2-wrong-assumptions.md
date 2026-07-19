# A2 - Shrink Wrong Assumptions (N calls -> 1)

Source data: `tool_errors_hist.txt` (1043 avoidable failed tool calls), `tool_errors.txt`
(1044 lines), `corrections.txt` (295 lines, mostly grep-dup noise). Structural facts verified
on disk 2026-07-19 (module layout, gradlew/settings placement, source-package roots).

## 1. Failure-mode taxonomy (by frequency)

Histogram (avoidable failures, this meta-session excluded):

| Shape | Count | Bucket | Root wrong-assumption |
|-------|------:|--------|-----------------------|
| No such file or directory | 492 | A | Bash `find`/`grep`/`ls`/`cat`/`cd`/`./gradlew` on a GUESSED path |
| has not been read yet | 220 | B | Edit/Write issued before Read of the target's current path |
| String to replace not found | 152 | C | `old_string` is stale / not the current bytes |
| File does not exist | 120 | A | Read/Edit tool on a GUESSED path (Read tool, not shell) |
| command not found | 34 | A | `python`/`jq`/`strings`/`rg`/`java-library` assumed present |
| InputValidationError | 22 | D | numeric/array param passed as string (offset, questions) |
| Found 2 matches | 12 | C | `old_string` not unique, `replace_all` not set |
| failed with exit code | 7 | - | genuine build/test failure (not an assumption bug) |
| Read the file first (x3) | 3 | B | same as B (variant wording) |

Buckets: **A path-guessing = 612 + 34 = 646 (~62%)**, **B read-before-edit = 223 (~21%)**,
**C old_string = 164 (~16%)**, **D validation = 22 (~2%)**. A+B+C = 97% of all avoidable
failures and all three have mechanical, low-context countermeasures.

## 2. Bucket A - path guessing (646 = 62%)

Distinct evidence-backed sub-causes (all traceable in `tool_errors.txt`):

- **A1 wrong package root / dead namespace.** The five family modules use FIVE different
  top package roots (verified sec.7). Claude guesses one root for all. Concrete misses:
  `find 'src/main/java/dev/sbs/discordapi/context'` (dead - moved to `dev.simplified.discordapi`),
  `dev/sbs/discordapi/handler`, `dev/sbs/discordapi/response` (6 `dev/sbs` hits total).
  asset-renderer is `lib/minecraft/renderer/...` not `dev/...`; hypixel/mojang are
  `api/simplified/...`. Root cause: no single lookup table of module -> src root.
- **A2 backslash-collapsed Windows paths in bash.** `grep: W:WorkspaceJavaSimplifiedMinecraft-Libraryasset-renderer/src/...`
  - a `W:\Workspace\...` path pasted into the git-bash Bash tool; bash ate every `\` as an
  escape, collapsing the path. 197 line-hits (concentrated in a few multi-file grep incidents,
  each printing ~15 collapsed paths). One rule ("forward-slash absolute paths only in bash")
  eliminates the class.
- **A3 `/tmp/` scratch misses (49 hits).** `/tmp/parity_baseline.tsv`, `/tmp/head_models.json`,
  `/tmp/vanilla_ids.txt`, `/tmp/phase7-resume.txt` - scratch files referenced that were never
  created this session (or cleaned between sessions). `/tmp` = `W:\tmp` here; cross-session
  survival is not guaranteed. Create-in-same-command or use the session scratchpad.
- **A4 `./gradlew` from the wrong dir.** `/bin/bash: line 1: ./gradlew: No such file or directory
  COMPILE_EXIT=127` and PowerShell `.\gradlew.bat is not recognized`. Every module is its OWN
  Gradle build (own `gradlew` + `settings.gradle.kts`, sec.7) - gradlew only exists at a MODULE
  root, never at a package/notes subdir and (mostly) not usefully at the family root for a
  single module. 4 direct hits + this is the dominant cause behind ad-hoc `cd "W:/.../module" && ./gradlew`.
- **A5 `command not found` (34).** `python` (box has `python3` only), `jq` (absent - use
  `python3 -c`), `strings` (absent), `rg` (present but grep is used 3852x vs rg 34x),
  `java-library` (mis-split gradle line). A 4-line tool-availability note removes these.

**N->1:** today a miss becomes find(fail) -> find(fail) -> ls -> Glob -> Read = 3-5 calls.
With a Project Map lookup + forward-slash rule, the first Read/grep hits. Savings scale with
the 646 count: even at a conservative 2 wasted calls each, ~1300 tool calls / session-cohort.

## 3. Bucket B - Edit-before-Read (223 = 21%)

`File has not been read yet. Read it first before writing to it.` - 220 + 3 variants.
The Edit/Write tools gate on a prior Read of the file at its CURRENT path. Triggers seen:

- First Edit of a session on a file touched in a prior session (no Read this session).
- Editing a file after a `git mv`/`rename_refactoring` moved it - the old-path Read no longer
  counts; must re-Read at the new path (already a CLAUDE.md rule, count shows it is under-applied).
- Write to overwrite a file that was never Read (Write on an existing file also gates).

**Interplay with IntelliJ MCP (the N->1 lever):** the IDE editing tools do NOT require the
Read gate - `mcp__IntelliJ_IDE__replace_text_in_file`, `apply_patch`, and `rename_refactoring`
operate directly, and `rename_refactoring` moves files + updates all usages atomically (no
per-file Read/Edit at all). So: bulk cross-file renames route to IntelliJ (already the
`java-bulk-rename` skill's job); for single-file edits the fix is simply "Read once at the top
of the first edit to a file, then batch all Edits to that file." Because `cco`/harness already
tracks file-read state, the rule is one Read per file per session, not per Edit.

**N->1:** each occurrence is exactly one wasted Edit that must be re-issued after a Read =
+1 Read + re-Edit. 223 occurrences ~= 223 wasted calls, removable by a single discipline line.

## 4. Bucket C - stale / non-unique old_string (164 = 16%)

`String to replace not found in file` (152) + `Found 2 matches ... replace_all is false` (12).

Evidence shows these cluster in TWO content types, not production code:

- **Markdown notes/plans/resume-prompts (majority).** `## 18. Self-check against the 85+ rubric`,
  `## 2. C1-C6 criteria`, `## 3. entity_geometry.json`, YAML front-matter `name:` blocks,
  progress-table rows `| 7 | BlockStateLoader reshape ... | pending |`, ASCII-tree lines
  `│   │   │   ├── asset/`. These files are edited from memory of a structure that has since
  changed, or the heading was never written exactly as guessed.
- **Javadoc blocks in .java.** ` * A larger inflate is a real vanilla {@code CubeDeformation}`,
  ` * <li><b>Class-hierarchy walks</b> - {@link #walkConstructorChain ...}` - multi-line
  `old_string` reconstructed from memory with a wrong leading-space count or a since-normalized
  ` - ` vs `--`. Whitespace/indent drift is the silent killer for multi-line javadoc matches.
- **`Found 2 matches`**: repeated import lines, repeated table separators, a symbol appearing
  twice - `replace_all` was the intended semantics but not set.

**N->1:** the anti-pattern is retry-the-guess (Edit fail -> Edit fail -> Read -> Edit). The fix
is Read-the-region-first-when-uncertain and copy exact bytes; for intentional all-occurrence
swaps set `replace_all: true` on the first call. A failed Edit should trigger a Read, never a
second guess. 164 occurrences, most costing 1-2 wasted Edits each.

## 5. Bucket D - InputValidationError + misc (22)

`Read failed ... 'offset' expected number but provided as string` (repeated) and
`AskUserQuestion ... 'questions' expected array`. Pure schema-type slips: numeric params
(`offset`, `limit`) must be JSON numbers, array params must be arrays. One-line reminder;
low volume, low priority. Not worth its own CLAUDE.md line - fold into a single terse note or
omit. Excluded from the primary draft to keep context dense.

## 6. Durable semantic wrong-assumptions from corrections.txt

corrections.txt is ~90% grep-duplication (the same ~8 render-parity corrections repeated
across sessions). The durable, CLAUDE.md-worthy facts (a fact that, if written once, prevents
a repeated wrong turn) are:

1. **Dead `dev.sbs.*` namespaces.** "CLAUDE.md's entire JPA persistence tier is aspirational
   and still on the old `dev.sbs` namespace - nothing real to preserve" + the `dev/sbs/discordapi`
   find misses. discord API is now `dev.simplified.discordapi`; persistence is
   `dev.simplified.persistence`; minecraft-api was split. **-> Project Map pins the live roots.**
2. **Stale code comments trusted as truth.** Recurring: "based on a stale code comment",
   "my analogy was based on a stale code comment - thanks for catching it", "based on a code
   comment that may be stale. Let me verify". **-> discipline rule: verify behavior against
   current source, not comments/memory, before asserting.**
3. **API/builder signatures guessed.** "The builder method signature isn't what I assumed",
   "`.withValue(...)` isn't the mechanism I assumed", "`Matrix4f.get(col,row)` indexes
   differently than I assumed". **-> route signature questions to IntelliJ `get_symbol_info`
   before writing the call (java-symbol-search skill already exists; under-used: 1 search_symbol
   call in the whole histogram).**
4. **Task/prompt headers naming the wrong file.** "The task header names the wrong file" (wanted
   `04-classification.md`, header said `00-overview.md`). **-> not durable; per-task. No rule.**
5. **Hibernate `hibernate.javax.cache.*`** correction is ALREADY captured in MEMORY.md at length;
   no new CLAUDE.md line needed - reference memory.

Only items 1-3 justify durable global text. Item 1 is folded into the Project Map; items 2-3
into a two-line "Verify before asserting" discipline note.

## 7. Verified structural facts (Project Map source of truth)

Verified on disk 2026-07-19. Every module is a STANDALONE Gradle build (own `gradlew` +
`settings.gradle.kts`); run `./gradlew` from the module root. Source-package roots are
heterogeneous - this is the #1 reason path guesses miss.

| Module (abs root under W:/Workspace/Java/Simplified/) | src package root (`src/main/java/`) |
|---|---|
| Minecraft-Library/asset-renderer | `lib/minecraft/renderer` |
| Minecraft-Library/minecraft-text | `lib/minecraft/text` |
| Minecraft-Library/nbt-factory | `lib/minecraft/nbt` |
| Minecraft-Library/vanilla-reference-harness | (harness) |
| Simplified-Dev/discord4j-framework | `dev/simplified/discordapi` |
| Simplified-Dev/persistence | `dev/simplified/persistence` |
| Simplified-Dev/spring-framework | `dev/simplified/serverapi` |
| Simplified-Api/hypixel | `api/simplified/hypixel` |
| Simplified-Api/mojang | `api/simplified/mojang` |
| Simplified-Api/skyblock (skyblock-data) | `dev/sbs/skyblockdata` |
| SkyBlock-Simplified/simplified-bot | `dev/sbs/simplifiedbot` |
| SkyBlock-Simplified/simplified-server | `dev/sbs/simplifiedserver` |
| SkyBlock-Simplified/simplified-data | `dev/sbs/simplifieddata` |
| SkyBlock-Simplified/sbs-api | (sbs-api) |

Common per-module paths (fill `<module>` from above):
- gradlew: `<module>/gradlew` (Bash) ; test XML: `<module>/build/test-results/test/*.xml`
- resources: `<module>/src/main/resources` ; tests: `<module>/src/test/java/<same pkg root>`
- asset-renderer resources live UNDER the pkg path: `.../src/main/resources/lib/minecraft/renderer/*.json`
  (block_defaults/geometry/items/models/tints.json).
- asset-renderer generated cache tree (do NOT hand-guess sub-paths; `find` inside it):
  `Minecraft-Library/asset-renderer/cache/asset-renderer/{vanilla/26.1,packs,diagnostics}`,
  plus `cache/dragon-extract/` (generated on demand - may be absent).
- Other family roots (Simplified-Dev, Simplified-Api, SkyBlock-Simplified, Minecraft-Library)
  each ALSO hold a top-level `gradlew`+`settings.gradle.kts` (composite include-build); a
  single module compiles fastest from ITS OWN root.
- memory: `C:/Users/BrianGraham/.claude/projects/W--Workspace-Java-Simplified/memory/`
  (`MEMORY.md` index + `architecture_simplified_data_initiative.md`, `discord-api-refactor.md`,
  `minecraft-api-split.md`, `org-restructuring-initiative.md`, `simplified-container-migration.md`).

## 8. Countermeasures -> deliverables map (N->1 accounting)

| Bucket | Count | Countermeasure | Deliverable | N->1 |
|---|---:|---|---|---|
| A1 pkg root | ~200 | Project Map table (module->src root) - look up, don't guess | CLAUDE.md `## Project Map` | 3-5 -> 1 |
| A2 backslash path | ~197 hits | rule: forward-slash abs paths in bash; never paste `W:\...` | CLAUDE.md `## Path & Shell` | 1 fail -> 0 |
| A3 /tmp scratch | 49 | rule: scratch in session dir; create-in-same-cmd | CLAUDE.md `## Path & Shell` | 1 fail -> 0 |
| A4 gradlew dir | ~6+ | rule + Map: gradlew at MODULE root only | CLAUDE.md `## Path & Shell` | 1 fail -> 0 |
| A5 cmd not found | 34 | rule: python3/jq-absent/rg-ok/strings-absent | CLAUDE.md `## Path & Shell` | 1 fail -> 0 |
| B read-before-edit | 223 | rule: 1 Read per file per session before Edit; IDE tools skip gate | CLAUDE.md `## Read/Edit Discipline` | 2 -> 1 |
| C old_string | 164 | rule: exact current bytes; fail->Read not retry; replace_all | CLAUDE.md `## Read/Edit Discipline` | 2-3 -> 1 |
| semantic 2-3 | n/a | rule: verify source/signatures (IDE) not comments/memory | CLAUDE.md `## Read/Edit Discipline` | prevents rework |

**Optional helper** `DRAFT-locate-java.sh`: given a bare class/file name, prints its absolute
path across ALL module source roots in ONE call (forward-slash safe) - collapses the
find(fail)->find(fail)->Glob loop for the "where does class X live" case that the Project Map
table can't answer (specific class within a known-root module). Complements, does not replace,
the table.

Primary deliverable: `DRAFT-assumptions-CLAUDE-md.md` - three dense sections (`## Project Map`,
`## Path & Shell Discipline`, `## Read/Edit Discipline`) sized to earn their per-session context
cost. Estimated cost ~55 lines; estimated saving 600-1000+ failed tool calls per comparable
session-cohort (each failed call = wasted input+output tokens + a recovery turn).
