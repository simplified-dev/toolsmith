# A4 - java-file-mover skill + faithful import reorder

Deliverables (all in `../` scratchpad root):
- `DRAFT-reorder_imports.py` - IDE-independent reorderer, IntelliJ Default layout, validated
- `DRAFT-import-order.md` - empirically-derived order + citations + divergence analysis
- `DRAFT-java-file-mover-SKILL.md` - flagship move/relocate/rename skill

## 1. Empirical method (what was sampled)

Authoritative-order justification (from established context): both
`.idea/codeStyles/codeStyleConfig.xml` set `PREFERRED_PROJECT_CODE_STYLE=Default`,
no custom `IMPORT_LAYOUT_TABLE`, no `.editorconfig`. So the order = IntelliJ
built-in Default; runtime truth = `reformat_file`/Optimize-Imports; the no-IDE
order is derived from committed files.

48 files read across all four family roots and many modules:
- 8 full import blocks: `GsonFactoryTest` (gson-extras), `PixelBuffer` +
  `BmpImageWriter` (image), `SnbtSerializer` (nbt-factory), `SkyBlockMember`
  (hypixel), `StringUtil` (utils), `MinecraftFont` (minecraft-text),
  `TypeRegistrar` + `ApiKeyAuthenticationFilter` (persistence/spring).
- 40 more via `find <4 roots> -path '*/src/*' -name '*.java' | grep -v build |
  sort | awk 'NR%53==0'` -> dumped `package`+`import` lines to
  `../import_blocks_sample.txt`.

Files deliberately chosen to include: `import static` + normal mixes, and the
full prefix spread `java. javax. jakarta. api. com. dev. net. io. lib. org.
lombok. discord4j. reactor.`.

## 2. Derived IntelliJ Default import order (the rules)

Three blank-line-separated groups; blank only between non-empty groups:
1. **all other** non-static imports, ASCII-sorted by full path
2. **javax.\*** (sorted) then **java.\*** (sorted) - one group, two sub-blocks
3. **all static** imports, ASCII-sorted

Load-bearing, non-obvious findings (full citations in DRAFT-import-order.md):
- **javax before java** and it is NOT alphabetical - proof `BmpImageWriter` L13-15
  (`javax.imageio.ImageIO` then `java.awt.*`). A flat sort ranks `java.`<`javax`.
  => group 2 must be emitted as two sub-blocks, not one sorted list.
- **Only exact `java`/`javax` are special.** `jakarta.*` sits in group 1 sorted
  among `dev`/`lombok`/`org` (`TypeRegistrar` L8; `ApiKeyAuthenticationFilter`
  L3-6). Same for `io`, `net`, `api`, `discord4j`, `reactor`.
- **Sort = flat-string ASCII == Java String.compareTo (case-sensitive).**
  Decisive proof `ApacheClientFactory` L16-19: under `...core5.http`, classes
  `HttpRequestInterceptor`(H=72) and `URIScheme`(U=85) sort BEFORE sub-packages
  `config`(c=99) and `protocol`(p=112). Uppercase-before-lowercase at equal
  depth is only produced by a plain string compare, not a case-insensitive or
  packages-first sort.
- **Wildcards preserved, sort in place** by path incl. trailing `.*`
  (`*`=42 < `.`=46 < letters): `PixelBuffer` L8 `java.awt.*` before
  `java.awt.color.*`; `SkyBlockMember` L4 `...member.*` before `...member.attribute`.
- **Static = one trailing group** regardless of package (`SnbtSerializer` L15
  `import static lib...SnbtConstants.*`). No `import static java...` exists in
  the codebase.
- Real drift found (tool has genuine work): `BlockModelReader` L5 has
  `dev.simplified.gson.JsonTree` sitting after a `lib.*` import;
  `JacobsCommand` orders `dev.simplified.*` before `dev.sbs.*` (wrong: `b`<`i`).

## 3. Divergences vs javadoc-normalize and sortimports.py

**javadoc-normalize `_inject_imports` (normalize.py L269-377)** - an inserter,
not a reorderer, but it re-`sort()`s groups:
1. Java-vs-javax INVERTED: `g.sort()` on `"import ..."` lines yields `import
   java.*` before `import javax.*` - opposite of the real rule (R2).
2. `_top_prefix()` strips `static `, so a new non-static `org.Foo` can be routed
   into a trailing `import static org.*` group and interleaved by `.sort()` -
   IntelliJ keeps statics strictly last/separate (R6).
3. Otherwise compatible here because group 1 is one block. reorder_imports.py is
   a strict superset (re-sorts the whole region).
4. Aside: `DEFAULT_PREFIXES` (L78-80) omits `api` and `jakarta`, both real roots
   -> FQN-javadoc auto-import misses them until `--prefix api --prefix jakarta`.

**naive sortimports.py**: sorts each blank-line run by full line string.
1. Static imports interleave at the `s` position (violates R6).
2. Never relocates across groups -> a misfiled `java.*` or an out-of-order group
   stays wrong (violates R1/R2).
3. No javax-before-java (R2). 4. Case-sensitive ASCII within a run is the one
   rule it matches, by accident.

## 4. reorder_imports.py - design + validation evidence

Algorithm: detect newline (CRLF/LF) + trailing-newline from raw bytes; decode
utf-8 (bail otherwise); normalize to LF; find `[first import .. last import]`
span; if a non-blank non-import line sits inside -> SKIP (protect comments);
bucket into javax/java/other/static; ASCII-sort each bucket by PATH (strip
`import `/`import static ` so key = dotted path, matching compareTo); dedupe;
emit group1=other, group2=javax+java, group3=static, joined by one blank;
splice back, restore newline + trailing newline; write only if changed.
Modes: default write, `--check` (gate, exit 1 if any would change), `--diff`.
Accepts files/dirs/globs; skips build/generated dirs.

**Validation run (all passed, python 3.14.3):**
- 4 already-correct control files (`GsonFactoryTest`, `MinecraftFont`,
  `BmpImageWriter`, `TypeRegistrar`) -> 0 changes (no false positives).
- 2 known mis-sorted (`BlockModelReader`, `JacobsCommand`) -> flagged by
  `--check`, fixed correctly by `--diff`/write: `dev.simplified.gson.JsonTree`
  moved between `com`/`lib`; `dev.sbs.*` moved above `dev.simplified.*`.
- Idempotence: re-`--check` after write -> `0 would reorder`, exit 0.
- Scrambled synthetic (static-in-middle, java-before-javax, jakarta-in-dev,
  wildcard) -> exact canonical 3-group output.
- CRLF file -> byte-level `xxd` confirms `0d0a` preserved through the rewrite.
- Comment inside import block -> `skipped (non-import line inside import block)`,
  file untouched.
- No-final-newline file -> trailing byte preserved (no newline added).

## 5. java-file-mover skill - structure + the load-bearing facts

Covers ~100% of move cases via a decision tree:
- **Case A** rename-in-place -> delegates to `java-bulk-rename`.
- **Case B** intra-module move (same git repo) -> `git mv` + package rewrite +
  import mechanics + verify tail.
- **Case C** cross-module move (different git repo) -> `git rm` in source +
  `git add` in dest (git mv CANNOT span repos) + gradle dependency edge +
  cycle check + root-build verify.

The single most load-bearing empirical fact, verified this session:
**git repos are per MODULE** (`Simplified-Dev/persistence/.git`,
`Minecraft-Library/asset-renderer/.git`, `Simplified-Api/hypixel/.git`, ...),
and `SkyBlock-Simplified/` is itself a repo alongside its module repos. So a
cross-module move is a cross-repo move - `git mv` errors, and history cannot
follow. This is what makes Case C genuinely different from Case B and is the
thing most likely to be gotten wrong. Owning-repo resolution baked into the
skill: `git -C "$(dirname "$FILE")" rev-parse --show-toplevel`.

"Four edits people forget" section enumerates the non-obvious import updates: FQ
import flip (#1), old-package-wildcard importers that need an ADD not a flip
(#2), former same-package siblings that need a new import (#3), and the moved
file's own imports for its former siblings (#4, compile-fail-only). Added
imports are placed unsorted and normalized by `reorder_imports.py`.

Delegation (no re-implementation): `java-find-usages`/`java-symbol-search`
(discovery), `java-bulk-rename` (Case A identifier), `reorder_imports.py` or
`reformat_file` (ordering), `javadoc-normalize` (javadoc FQNs),
`java-import-audit` (leftover unused), `gradle-verify-gate` (compile+test).
Read-before-edit re-Read rule after any git mv / IDE move is called out
explicitly (CLAUDE.md `## File Editing`).

## 6. Install steps

```bash
mkdir -p ~/.claude/skills/java-file-mover
cp DRAFT-java-file-mover-SKILL.md ~/.claude/skills/java-file-mover/SKILL.md
cp DRAFT-reorder_imports.py       ~/.claude/skills/java-file-mover/reorder_imports.py
```
The skill references `~/.claude/skills/java-file-mover/reorder_imports.py`, so
keep the script beside `SKILL.md`. DRAFT-import-order.md is reference doc; keep
it in the skill dir or memory - it is the citation trail for the order.

## 7. Open follow-ups
- Fold `api` + `jakarta` into javadoc-normalize `DEFAULT_PREFIXES` (or document
  `--prefix`) - unrelated to the reorderer but surfaced here.
- Optional: fix javadoc-normalize `_inject_imports` java/javax inversion and the
  static-group misroute; low frequency, not blocking.
- reorder_imports.py could later grow a `--stdin`/`--print` mode for piping, but
  file-list mode covers the move workflow.

