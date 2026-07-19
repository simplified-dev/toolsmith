# IntelliJ Default import order - empirically derived

Authoritative because both `.idea/codeStyles/codeStyleConfig.xml` set
`PREFERRED_PROJECT_CODE_STYLE=Default` with **no** custom `Project.xml`
`IMPORT_LAYOUT_TABLE` and **no** `.editorconfig` anywhere. The order below IS
the IntelliJ built-in Default scheme, so `mcp__IntelliJ_IDE__reformat_file` /
Optimize-Imports is the runtime source of truth; this document is the no-IDE
fallback, derived from real committed `.java` files.

## The layout (3 groups, blank-line separated)

```
<group 1: all other non-static imports>        ASCII-sorted by full path
<blank line>
<group 2: javax.* then java.*>                 javax block ASCII-sorted, then java block ASCII-sorted
<blank line>
<group 3: all static imports>                  ASCII-sorted by full path
```

- A blank line appears **only between non-empty groups**. A file with just
  `java.*` imports emits one block, no surrounding extra blanks.
- Group boundaries are the only blank lines inside the import region; group 1,
  group 2, and group 3 are each a single contiguous run in every file sampled.

## Rules, each with a citation

Sample = 48 committed files across all four family roots (8 read in full +
40 from a stratified `find | awk 'NR%53==0'` sweep).

### R1. Three groups, static last
`Simplified-Dev/gson-extras/.../GsonFactoryTest.java`: group1 `com/dev/lombok/org`
(L3-21), blank L22, group2 `java.util.Optional` (L23), blank L24, group3
`import static org.hamcrest.*` (L25-26).

### R2. javax.* sorts BEFORE java.*  (this is NOT alphabetical)
`Simplified-Dev/image/.../codec/bmp/BmpImageWriter.java` L13-15:
`javax.imageio.ImageIO`, then `java.awt.*`, then `java.awt.image.BufferedImage`.
Also `Minecraft-Library/minecraft-text/.../font/MinecraftFont.java` L12-13:
`javax.imageio.ImageIO` then `java.awt.Font`.
A flat string sort would rank `java.` before `javax` (`.`=46 < `x`=120), so
group 2 MUST be emitted as the javax sub-block then the java sub-block, each
independently sorted - it is not one sorted list.

### R3. Only exact top segments `java` and `javax` are special-cased
`jakarta.*` lands in group 1, sorted alphabetically among the other packages:
- `Simplified-Dev/persistence/.../type/TypeRegistrar.java` L8: `jakarta.persistence.Convert`
  sits between `dev.simplified.reflection.*` (L7) and `org.hibernate.*` (L9).
- `Simplified-Dev/spring-framework/.../security/ApiKeyAuthenticationFilter.java`
  L3-6: `jakarta.servlet.*` is the FIRST group-1 block (before `lombok`, `org`)
  because `j` < `l` < `o`.
Other non-java roots seen in group 1: `api` (hypixel), `com`, `io` (io.sentry),
`net` (net.minecraft), `discord4j`, `reactor`, `org`, `lombok`, `lib`.

### R4. Sort is flat-string ASCII == Java String.compareTo (case-sensitive)
`Simplified-Dev/client/.../factory/ApacheClientFactory.java` L16-19, all under
`org.apache.hc.core5.http`:
```
org.apache.hc.core5.http.HttpRequestInterceptor   # 'H' = 72
org.apache.hc.core5.http.URIScheme                # 'U' = 85
org.apache.hc.core5.http.config.RegistryBuilder   # 'c' = 99
org.apache.hc.core5.http.protocol.HttpContext     # 'p' = 112
```
The upper-cased class segments (`Http...`, `URIScheme`) sort BEFORE the
lower-cased sub-packages (`config`, `protocol`) at the same depth. This is the
signature of a plain string `compareTo` on the full dotted path - a
case-insensitive or segment/packages-first sort would order these differently.
Corroborated by `GsonFactoryTest` L4-5: `com.google.gson.JsonObject` ('J'=74)
before `com.google.gson.annotations.SerializedName` ('a'=97).

### R5. Wildcards are preserved verbatim and sort in place by path
`Simplified-Dev/image/.../pixel/PixelBuffer.java` L8: `java.awt.*` precedes
`java.awt.color.ColorSpace` (`*`=42 < `c`). `SkyBlockMember.java` L4:
`...member.*` precedes `...member.attribute.AttributeShards` (`*`=42 < `a`).
The codebase contains author-written wildcards (`java.awt.*`, `java.util.*`,
`lib.minecraft.nbt.tag.*`, `org.hamcrest.Matchers.*`, `SnbtConstants.*`). With
no `.editorconfig`/checkstyle and no classpath knowledge offline, the reorderer
NEVER expands or collapses them - it keeps each wildcard line and sorts it by
its path (the trailing `.*` included).

### R6. Static imports form ONE trailing group regardless of package
`Minecraft-Library/nbt-factory/.../snbt/SnbtSerializer.java` L15:
`import static lib.minecraft.nbt.io.snbt.SnbtConstants.*` sits alone below the
java group, even though it is a `lib.*` package. No file in the codebase has a
`import static java...` line, so the java-vs-other split does not apply to
statics; they are one block. (IntelliJ Default only ever emits one static row:
"import static all other imports".)

## Divergences from the two existing ad-hoc tools

### (a) `javadoc-normalize` `_inject_imports` (normalize.py L269-377)
That function is an INSERTER (adds FQN-auto-imported types into existing
groups), not a full reorderer, but it re-sorts every group it rebuilds, so its
ordering assumptions are comparable:

1. **java-vs-javax ordering is inverted.** It ends with `for g in groups:
   g.sort()`, a plain `list.sort()` on full `"import ..."` line strings. For the
   java/javax group that yields `import java.*` BEFORE `import javax.*` (`"import
   java."` < `"import javax"`), the OPPOSITE of R2. Reproduces the bug only when
   it injects into a file that has a javax member; low-frequency but real.
2. **A new non-static import can be misrouted into the static group.**
   `_top_prefix()` strips the `static ` prefix, so a trailing group of
   `import static org.hamcrest...` reports top-prefix `org`; step 1 of `_route`
   ("a group already containing the same top prefix") then appends a new
   non-static `org.Foo` into that static block, which `.sort()` interleaves
   among the statics. IntelliJ keeps statics strictly last and separate (R6).
3. Its grouping is otherwise compatible because group 1 is a single block in
   this codebase; the reorderer here is a strict superset - it re-sorts the
   whole region, not just injected lines.

The reorderer here does NOT auto-import FQNs; that remains `javadoc-normalize`'s
job. The two compose: run `javadoc-normalize --fix` first (FQN -> import), then
`reorder_imports.py` to canonicalize the resulting block.

### (b) naive `sortimports.py` (Claude's recovered throwaway)
"Alphabetizes each contiguous run of `import ...;` lines by full stripped line
string; interleaves `import static` with normal imports; never reorders across
blank-line groups." Divergences from IntelliJ Default:

1. **Static imports interleave at the `s` position.** It sorts by the whole line
   including the `import static ` prefix; `"import static X"` sorts wherever
   `import s...` falls (after `import c/d/...`, before `import z...`), so a
   static import lands mid-block instead of in the trailing group (violates R6).
2. **Never relocates across groups.** It sorts each blank-line-delimited run in
   place, so a `java.*` import misfiled in group 1, or a whole out-of-order
   group, is left wrong (violates R1/R2). The reorderer here re-partitions the
   entire region.
3. **No javax-before-java rule** (violates R2).
4. It IS case-sensitive ASCII within a run (Python default), so R4 happens to
   match - the only rule it gets right by accident.

## Note for `javadoc-normalize` and `java-import-audit`
`normalize.py` `DEFAULT_PREFIXES` (L78-80) lists `java, javax, com, org, net,
dev, io, lib` but OMITS `api` and `jakarta`, both of which are real top-level
roots in this codebase (`api.simplified.hypixel.*`, `jakarta.persistence.*`).
FQN javadoc refs into those roots will not be auto-imported until the prefix
list is extended (`--prefix api --prefix jakarta`, or add them to the default).
This does not affect `reorder_imports.py` (it treats every non-java/javax root
identically) but is worth folding into the audit skill.
