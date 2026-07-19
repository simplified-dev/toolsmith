<!--
DRAFT - exact text to paste into C:/Users/BrianGraham/.claude/CLAUDE.md (user-global).
Targets 3 wrong-assumption failure buckets that cost ~1030 avoidable failed tool calls:
  path-guessing 646, Edit-before-Read 223, stale/non-unique old_string 164.
Every line is priced against per-session context; keep it dense. Verified on disk 2026-07-19.
Paste the three ## sections below verbatim. They are self-contained.
-->

## Project Map (look paths up here - do not guess)
Root: `W:/Workspace/Java/Simplified/`. Each module is a STANDALONE Gradle build (own `gradlew` +
`settings.gradle.kts`); run `./gradlew` from the **module root only** (never a package/notes
subdir; family roots hold a composite build but a single module compiles fastest from its own
root). Source-package roots differ per module and are the #1 cause of path misses. Package root =
`<family-prefix>/<leaf>`; family prefix by root dir: `Minecraft-Library`=`lib/minecraft`,
`Simplified-Api`=`api/simplified`, `Simplified-Dev`=`dev/simplified`, `SkyBlock-Simplified`=`dev/sbs`.
For most modules the leaf = the module dir name (`client`, `dataflow`, `expression`, `image`,
`manager`, `persistence`, `reflection`, `scheduler`, `yaml`, `github`, `hypixel`, `mojang`). TRAP
leaves (dir name != package leaf) - do NOT guess these:

| Module dir | `src/main/java/` pkg root |
|---|---|
| `Minecraft-Library/asset-renderer` | `lib/minecraft/renderer` |
| `Minecraft-Library/minecraft-text` | `lib/minecraft/text` |
| `Minecraft-Library/nbt-factory` | `lib/minecraft/nbt` |
| `Minecraft-Library/vanilla-reference-harness` | `lib/minecraft/refharness` |
| `Simplified-Dev/collections` | `dev/simplified/collection` (singular) |
| `Simplified-Dev/utils` | `dev/simplified/util` (singular) |
| `Simplified-Dev/gson-extras` | `dev/simplified/gson` |
| `Simplified-Dev/discord4j-framework` | `dev/simplified/discordapi` |
| `Simplified-Dev/spring-framework` | `dev/simplified/serverapi` |
| `Simplified-Api/skyblock` | `dev/sbs/skyblockdata` |
| `SkyBlock-Simplified/{sbs-api, simplified-bot, simplified-data, simplified-server}` | `dev/sbs/{sbsapi, simplifiedbot, simplifieddata, simplifiedserver}` (no hyphen) |

Full 34-module inventory + short aliases: `Simplified-Dev/toolsmith/src/toolsmith/modules.py`
(`PACKAGE_ROOTS`, `ALIASES`) and `toolsmith/notes/analysis/module-inventory.md`.

Per module (fill `<module>`): tests `<module>/src/test/java/<same pkg root>`; resources
`<module>/src/main/resources`; test XML `<module>/build/test-results/test/*.xml`; gradlew
`<module>/gradlew`. asset-renderer JSON data is UNDER the pkg path:
`.../src/main/resources/lib/minecraft/renderer/*.json`; its generated cache tree
(`asset-renderer/cache/asset-renderer/{vanilla/26.1,packs,diagnostics}`, `cache/dragon-extract/`)
is generated - `find` inside it, never hand-guess sub-paths. Memory:
`C:/Users/BrianGraham/.claude/projects/W--Workspace-Java-Simplified/memory/` (`MEMORY.md` index).
DEAD namespaces (do not path into): `dev.sbs.discordapi` (now `dev.simplified.discordapi`),
`dev.sbs.minecraftapi` (split), old `api/.../persistence` (now `dev.simplified.persistence`).
When a class's module is known but its file is not, Glob / IntelliJ `find_files_by_name_keyword`
BEFORE Read - one locate call beats a find-fail chain.

## Path & Shell Discipline
- **Forward-slash absolute paths in the Bash (git-bash) tool - always.** `W:/Workspace/...`,
  never `W:\Workspace\...`; bash eats each `\` as an escape and collapses the path
  (`W:WorkspaceJava...` -> No such file). PowerShell tool takes native `W:\...`.
- **gradlew runs from the module root** (see Project Map). Symptom of wrong dir:
  `./gradlew: No such file or directory` / `COMPILE_EXIT=127` / `.\gradlew.bat is not recognized`.
- **Scratch files go in the session scratchpad dir**, not `/tmp` (`/tmp` = `W:\tmp`, not
  guaranteed across sessions). If you must use `/tmp`, create-and-read in the SAME command; never
  reference a prior-session `/tmp/*.txt`.
- **Tool availability on this box:** `python` and `python3` both run (3.14); `jq` ABSENT - parse
  JSON with `python -c`; `strings` ABSENT; `rg` is a Bash-tool shell FUNCTION (it shells out to
  claude.exe), NOT a binary - it works inside the Bash tool but a child `bash script.sh` cannot use
  it and `grep -oP` errors on this locale, so any committed `.sh` must use plain `grep`/`sed`; JSON
  test-tally via `jtally` over `build/test-results/test/*.xml`.
- **Verify a path before Read/grep when unsure** (Glob or `ls -d`) rather than issuing a Read on
  a guessed path - a failed Read costs a full recovery turn.

## Read/Edit Discipline
- **Read a file once (at its CURRENT path) before the first Edit/Write to it this session**, then
  batch all Edits to that file. `git mv` / `rename_refactoring` invalidates the prior Read -
  re-Read at the new path. (Edit/Write gate on a same-session Read; Write on an existing file
  gates too.) IntelliJ MCP `replace_text_in_file` / `apply_patch` / `rename_refactoring` skip the
  gate - route bulk cross-file renames there (`java-bulk-rename`).
- **`old_string` must be the exact current bytes.** If an Edit returns "String to replace not
  found", Read the region and copy bytes verbatim - never retry a second guess. Multi-line
  javadoc/markdown fails on indent and ` - ` vs `--` drift; include enough surrounding context to
  be unique.
- **`Found N matches`** means set `replace_all: true` (when every occurrence should change) or
  extend `old_string` with a unique neighbor line (when only one should).
- **Verify behavior against current source, not comments or memory, before asserting it.** Stale
  code comments and remembered API/builder signatures are a recurring miss - for a method/field
  shape use IntelliJ `get_symbol_info` (`java-symbol-search`) rather than guessing the call.
- **Schema param types:** `offset`/`limit` are numbers, `questions`-style params are arrays -
  pass JSON types, not strings.
