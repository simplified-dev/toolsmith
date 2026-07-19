# Adversarial Verification - token-optimization audit drafts

Verifier ran real-file checks (Glob/Grep/Bash/Read), not draft-on-faith. Scope: every
draft except the 3 already-productionized ones (reorder_imports.py, jtally.py, gwverify.sh),
which were sanity-checked only. Environment ground truth confirmed on disk 2026-07-19.

**Headline:** No BLOCKERS. The audit's technical claims are unusually accurate - import
order (R1-R6), the git-repo topology, the phantom code-review-graph, and every skill-patch
claim all verified true against real files. The one systemic defect is **incomplete module
inventory** repeated across four artifacts: the "look here, don't guess" tables list only
~half the real modules, which silently re-enables the exact path-guessing they target.

Totals: **0 blocker / 2 major / 10 minor.**

---

## Ground truth re-established (evidence)

- Root is NOT a git repo; each module is its own `.git`. Verified: `persistence/.git`,
  `reflection/.git`, `asset-renderer/.git`, `hypixel/.git`, `SkyBlock-Simplified/.git`,
  `simplified-bot/.git` all separate. -> file-mover's whole premise is correct.
- gradlew placement: **root `W:/Workspace/Java/Simplified/gradlew` EXISTS** (+ family roots
  Simplified-Dev/Simplified-Api/SkyBlock-Simplified, and every module). Minecraft-Library
  family root has NO gradlew (its modules do). -> file-mover Case C "root ./gradlew build"
  is valid.
- `rg` **is a bash function** (`type rg` -> `rg is a function`), not a binary. `jq` ABSENT,
  `strings` ABSENT - both as drafts claim. `python` DOES run (Python 3.14.3) - drafts'
  "python3 not python" is inaccurate but harmless.
- `code-review-graph`: no live tool, no config entry. Only refs are 4 loose skill files +
  this session's own workflow json. **Confirmed phantom.**
- `~/.claude/data/pattern-index.json` and `~/.claude/scripts/calculate-confidence.sh` both
  **MISSING** (confirms skill-patch #9).

---

## DRAFT-import-order.md - SOLID

Every rule spot-checked against real committed files; all correct.

- **R1** (3 groups, static last): `gson-extras/.../GsonFactoryTest.java` - group1 com/dev/lombok/org
  L3-21, blank, `java.util.Optional` L23, blank, static L25-26. CONFIRMED.
- **R2** (javax BEFORE java): `image/.../bmp/BmpImageWriter.java` L13-15 `javax.imageio.ImageIO`
  -> `java.awt.*` -> `java.awt.image.BufferedImage`; `minecraft-text/.../MinecraftFont.java`
  L12-13 same. CONFIRMED - this is the counter-intuitive one and it holds.
- **R3** (jakarta in group1): `persistence/.../type/TypeRegistrar.java` L8 `jakarta.persistence.Convert`
  between `dev.simplified.reflection.accessor.FieldAccessor` (L7) and `org.hibernate.*` (L9);
  `spring-framework/.../ApiKeyAuthenticationFilter.java` L3-6 `jakarta.servlet.*` first in group1.
  CONFIRMED.
- **R4** (flat-string ASCII compareTo): `client/.../ApacheClientFactory.java` L16-19 under
  `org.apache.hc.core5.http` - `HttpRequestInterceptor`(H=72), `URIScheme`(U=85),
  `config.RegistryBuilder`(c=99), `protocol.HttpContext`(p=112). CONFIRMED - upper-case class
  segments before lower-case sub-packages = plain compareTo signature.
- **R5** (wildcards sort in place): `image/.../PixelBuffer.java` L8 `java.awt.*` before L9
  `java.awt.color.ColorSpace`; `hypixel/.../SkyBlockMember.java` L4 `...member.*` before L5
  `...member.attribute.AttributeShards`. Both CONFIRMED.
- **R6** (one trailing static block regardless of pkg): `nbt-factory/.../SnbtSerializer.java`
  L15 `import static lib.minecraft.nbt.io.snbt.SnbtConstants.*` alone below java group despite
  being a `lib.*` pkg. CONFIRMED.
- Reflection-module spot-check (per verifier brief): `reflection/.../FieldAccessor.java`
  group1 dev/lombok/org L3-7, blank, java L9-11 - matches Default. Persistence spot-check
  (TypeRegistrar) matches. Both projects confirmed on `PREFERRED_PROJECT_CODE_STYLE=Default`.
- The `normalize.py DEFAULT_PREFIXES` note (omits `api`, `jakarta`) is also accurate
  (DEFAULT_PREFIXES at L78; neither token present).

No defects. This is the strongest draft.

---

## DRAFT-assumptions-CLAUDE-md.md - NEEDS-FIX

- **MAJOR - Project Map table is incomplete, undermining its own purpose.** The table
  ("look paths up here - do not guess") lists 3 of ~18 Simplified-Dev modules. Omitted
  buildable modules with **non-obvious** pkg roots: `reflection`->`dev/simplified/reflection`,
  `client`->`dev/simplified/client`, `collections`->`dev/simplified/collection` (**singular**),
  `utils`->`dev/simplified/util` (**singular**), `gson-extras`->`dev/simplified/gson`,
  plus `annotations`, `image`, `dataflow`, `expression`, `manager`, `scheduler`, `yaml`,
  `toolsmith`; also `Simplified-Api/github`, `SkyBlock-Simplified/sbs-api`. The singular
  `collection`/`util`/`gson` roots are exactly the traps this table exists to kill, yet
  they're absent - Claude will still guess (and mis-guess `collections`->`.../collections`).
  Every entry that IS listed is correct (verified hypixel, mojang, persistence, spring,
  discord4j, asset-renderer, minecraft-text, nbt-factory, skyblock, simplified-bot/server/data).
  **Fix:** add the missing modules with their real (verified) pkg roots.
- **MINOR** - "python3 (not python)" is false; `python` resolves and runs (3.14.3). Harmless
  since python3 also works. Fix: drop the parenthetical or soften to "prefer python3".
- **MINOR** - "rg present but grep is standard here" is true only inside the Bash tool; rg is
  a function child scripts can't use. Not flagged here (it IS flagged in transcript-mine).
  Fix: add a half-line caveat so the CLAUDE.md reader doesn't put `rg` in a `.sh`.
- Convention check PASSES: no em-dashes, no `--`, no design-scaffolding refs; the failure-bucket
  metrics live in an HTML comment (not pasted) so they don't cost per-session context. Dense.
  git-mv/re-Read/Read-before-Edit rules are consistent with existing global CLAUDE.md, not
  contradictory.

---

## DRAFT-command-CLAUDE-md.md - NEEDS-FIX (mostly solid)

- Install wiring is internally consistent: `gw` sources `$HERE/modules.sh` and calls
  `$HERE/jtally.py`, and the install copies both to `~/.claude/bin/`. Correct.
- Consistent with existing CLAUDE.md's "cd in a compound command can trip the permission
  prompt" - reinforces it (bare `cd` once, then bare commands). No contradiction.
- **MINOR** - the pasted alias list (`ar mt nbt vrh d4j spring pers gson coll ann image
  mojang hypixel bot srv`) inherits modules.sh's incompleteness (~half the buildable modules
  absent). `gw .` (current dir) is the escape hatch, so not a blocker, but the CLAUDE.md text
  advertises the alias set as if complete.
- No em-dashes / scaffolding refs. Dense enough.

---

## DRAFT-java-file-mover-SKILL.md - SOLID (minor fixes)

The flagship. Repo topology, gradlew placement, and CLAUDE.md compliance all verified.

- Honors CLAUDE.md correctly: prefers `git mv`; re-Read after mv (called out repeatedly);
  delete-and-recreate explicitly the "last resort, untracked-only"; never `rm`+Write a
  tracked file. Decision tree (same-dir=A / same-repo=B / cross-repo=C) matches the real
  per-module `.git` topology.
- Grep fallbacks use `grep -rl/-rnE` with `\b` and `--include=*.java` - **no `grep -oP`**, so
  locale-safe; and the skill instructs Claude (runs in Bash tool), not a child `.sh`, so the
  rg-function limitation doesn't bite. Case C "root ./gradlew build" is valid (root gradlew
  exists).
- **MINOR** - Case C removes the source with `git -C "$srcmod" rm "$SRC"`. A strict reading
  of the verifier constraint "never deletes a git-tracked file" is technically breached, but
  cross-repo `git mv` is genuinely impossible and history can't follow across separate repos
  regardless; the skill states this. Acceptable. Fix: add an explicit "`git rm` needs the file
  clean/committed; `git stash` or `-f` otherwise" note (only partly covered by the status table).
- **MINOR** - Import-rewrite mechanics say "Edit each" importer; for a bulk FQN swap across
  many files CLAUDE.md prefers `sed -i` over Edit. Cross-reference skill-patch #4c's sed recipe
  rather than implying per-file Edit for large blast radii.
- **MINOR** - depends on a bundled `reorder_imports.py` copied into the skill dir; the
  productionized copy lives in `toolsmith`. Skill says keep in sync - fine, just an install note.

---

## DRAFT-skill-patches.md - SOLID (every claim verified true)

This is a recommendations/diff doc (not an installable file); correctness of its claims is
what matters, and all load-bearing ones check out:

- #2 java-exception-class-gen child template lacks `final` - `SKILL.md:143`
  `public class FooBarException extends FooException` (root at :74 correctly non-final). TRUE.
- #7 java-modifier-audit cites `scan-the-codebase-for-composed-tarjan.md line 19`
  (`SKILL.md:23-24`) - a real CLAUDE.md design-scaffolding violation. TRUE.
- #8 java-record-audit discovery regex is `^public\s+record\s+\w+` (`SKILL.md:68`) - misses
  non-public/nested/final records. TRUE.
- #9 pattern-recognition reads `~/.claude/data/pattern-index.json` (`:53`,`:63`) and refs
  `calculate-confidence.sh`; both files MISSING. knowledge-core.md refs present. TRUE.
- #3 javadoc-normalize `_inject_imports` (`:269`,`_top_prefix`:329) + DEFAULT_PREFIXES omit
  api/jakarta. Consistent with import-order note. TRUE.
- #11 jmh compare.py `_infer_direction` returns `True` by default (`compare.py:257,260`),
  `main()` has no `if not paired` guard between the compare call (`:308`) and return 0
  (`:321`) - empty-pair false-green is real. TRUE.
- #10 four loose files (debug-issue/explore-codebase/refactor-safely/review-changes .md) sit
  directly under `skills/`, are absent from the loaded skill listing, and reference
  code-review-graph tools. TRUE. **Crucially, the patch gates promotion on verifying the MCP
  and says "if NOT installed: delete all four" - it does NOT assume the phantom is installed.**
  Correct per ground truth.
- **MINOR** - the #1 "Module roots" table and text say "3 skill files" reference the graph in
  a couple of places; the tools are referenced by 4 loose files (only 1 uses the literal
  name). Cosmetic. Also the Simplified-Dev module-roots table (#1) omits reflection/client/
  utils/etc. - same inventory gap as the CLAUDE.md draft.

---

## DRAFT-mcp-memory-recommendations.md - SOLID

- Correctly identifies code-review-graph as a **phantom** and gates every graph recommendation
  on running `/mcp` first ("SKIP rewiring... unproven install"; "if the graph server is absent
  the premise is false"). Does NOT treat it as available anywhere. Exactly right.
- Live-MCP inventory (IntelliJ, DeepWiki, Gmail/Calendar/Drive, Postman) matches the actual
  environment. jdtls-lsp "enabled but dormant" is consistent with the stated setup.
- Verdicts (skills-as-MCP=narrow trial, mempalace=capture-yes/recall-defer, hygiene=adopt,
  graph=skip-rewire/trial-impact-only) are well-reasoned and align with the token goal.
- **MINOR** - "referenced by 3 skill files only" undercounts (4 files reference the tools).
  Cosmetic.
- **MINOR** - the optional `DRAFT-simplified-tools-mcp-server.py` scaffold uses fastmcp v2-style
  `@mcp.tool(readOnlyHint=...)` while the doc says "fastmcp 1.0" is present - possible API
  mismatch on install. `.mcp.json` uses `"command":"python"` (works) and requires renaming the
  DRAFT- file (noted in its JSON comment). Flagged as "trial later / optional", so not a blocker.

---

## DRAFT-transcript-mine-SKILL.md - SOLID

- Bakes in the exact environment facts, all verified: `rg` is a function (must run in the Bash
  tool, never `bash script.sh`), `grep -oP` locale error + 100k-char-line choke, the
  `((?:\\.|[^"])*)` JSON-string capture, position-independent `file_path`. Correct.
- Correctly ships as a SKILL, not a `.sh` (per fact #1) - the one draft that must not be a
  child script, and it isn't.
- **MINOR** - `-g '!*<CURRENT_SESSION_ID>*'` is a placeholder Claude must fill at runtime
  (documented). No defect.

---

## Helper scripts

### DRAFT-gw.sh - SOLID (minor)
- Sources modules.sh, calls jtally.py - paths consistent with the install block. Operates on a
  gradle log file with `grep -E/-nE/-vE` (no rg, no -oP). gw->jtally interface WORKS: gw passes
  absolute `$DIR`; `jtally.resolve_module` returns non-alias tokens verbatim (jtally documents
  `jtally /abs/path`). Auto-tally glob `*[Tt]est` correctly excludes `compileTestJava` (ends in
  "Java").
- **MINOR** - alias coverage inherited from modules.sh is partial (see below); `gw .` mitigates.
- Edge: `set -- "${ARGS[@]}"` on an empty array under `set -u` is safe on bash >=4.4 (git-bash
  is modern). Non-issue in practice.

### DRAFT-modules.sh - NEEDS-FIX
- **MAJOR (grouped with the Project Map gap)** - alias table missing ~13 buildable modules
  (reflection, client, dataflow, expression, manager, scheduler, utils, yaml, toolsmith,
  github, skyblock-data->skyblock, sbs-api, simplified-data). It's billed as the "single source
  of truth" yet the same 15-alias set is ALSO hard-copied into `jtally.py` (L28-44) - two
  divergent copies. Fix: extend the alias map and have jtally source/import one table, or accept
  jtally's copy is alias-only (gw passes absolute dirs, so functionally jtally's copy only
  matters for standalone `jtally <alias>`).
- SIMPLIFIED_ROOT override consistent with jtally.py. Good.

### DRAFT-locate-java.sh - SOLID
- Pure `find` over module `src` trees; forward-slash ROOT; handles bare name / `.java` /
  `--test` / `--res`. No rg/grep-oP - runs correctly as a child script. Directly targets the
  path-miss bucket.
- **MINOR** - it is NOT referenced by the CLAUDE.md install block (DRAFT-command lists gw/jtally/
  modules only) - an orphan tool that won't get installed or invoked. Fix: add it to the install
  list and mention it in the Project Map ("locate a class: `locate-java.sh <Name>`").

### DRAFT-recall-transcripts.sh - NEEDS-FIX (minor)
- **MINOR** - docstring "Prefer ripgrep (fast over ~400 MB)" but as a child `.sh` the rg
  function isn't inherited and there's no rg binary -> `command -v rg` fails -> falls back to
  `grep -i -m3 -H -n`. That fallback is correct and locale-safe (no `-P`/`-oP`), but the rg
  branch is dead and grep over 100k-char JSONL lines can be slow. Fix: state grep-only reality,
  or note the rg path only fires if a real rg binary is ever installed.
- Otherwise functions; `-r PROJECT` glob and newest-first `find -printf` are fine on git-bash.

### DRAFT-gwverify.sh - SOLID (already productionized/tested; sanity-checked)
- `find_gradlew` walks up to the nearest gradlew - supported by verified module/family/root
  gradlew placement. Captures true rc via temp-log-then-filter (not PIPESTATUS). No rg/grep-oP.
- Note: `BUILD ` is in the NOISE filter, so a "BUILD FAILED" line is stripped from the surfaced
  output - benign, because the `GATE: FAIL rc=<n>` trailer carries the verdict. No change needed.

---

## Issue ledger

| # | Sev | Draft | Problem | Fix |
|---|-----|-------|---------|-----|
| 1 | MAJOR | assumptions-CLAUDE-md | Project Map omits ~13+ modules incl. singular `collection`/`util`/`gson` roots - re-enables path guessing (the 646-miss bucket) | add all buildable modules w/ verified pkg roots |
| 2 | MAJOR | modules.sh (+ jtally.py copy, skill-patches #1 table) | alias/module tables cover ~half the modules, in two divergent copies | extend + single-source the alias map |
| 3 | minor | assumptions-CLAUDE-md | "python3 not python" false | soften/drop |
| 4 | minor | assumptions-CLAUDE-md | rg-is-a-function caveat missing | add half-line |
| 5 | minor | command-CLAUDE-md | advertised alias set incomplete | note `gw .` / extend |
| 6 | minor | java-file-mover | Case C `git rm` clean-file caveat thin | add stash/-f note |
| 7 | minor | java-file-mover | "Edit each" importer vs CLAUDE.md prefer-sed for bulk | cross-ref #4c sed recipe |
| 8 | minor | mcp-memory + skill-patches | "3 skill files" undercount (actually 4) | cosmetic |
| 9 | minor | mcp-memory | MCP scaffold fastmcp v2 API vs stated 1.0; rename-on-install | version-pin note |
| 10 | minor | recall-transcripts.sh | dead rg branch; docstring overpromises | grep-only note |
| (+) | minor | locate-java.sh | orphaned (not in install list) | wire into CLAUDE.md install |

**Single most important fix:** Complete the module inventory. The Project Map table in
DRAFT-assumptions-CLAUDE-md (and the alias tables in modules.sh / jtally.py / skill-patches #1)
must list **every buildable module with its real, verified pkg root** - most critically the
counter-intuitive singular roots `dev/simplified/collection`, `dev/simplified/util`,
`dev/simplified/gson`, and the missing `reflection`/`client`. A "don't guess, look here" table
that covers half the tree still forces guessing for the other half - defeating the #1 goal
(646 path-guessing misses) the whole CLAUDE.md addition is priced against.
