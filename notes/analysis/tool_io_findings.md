# Tool I/O findings (Edit / Read / Write / Grep INPUTS, not just counts)

Extracted by ripgrep from all transcripts under the prefix, EXCLUDING this meta-session.
NOTE: these ripgrep counts are HIGHER and more reliable than the earlier PowerShell
`tool_freq.txt` histogram, which undercounted tool_use blocks on very large single-line
JSONL records (PowerShell Select-String is unreliable on multi-MB lines; ripgrep is not).
Corrected counts below supersede tool_freq for Edit/Read/Grep.

## Headline

| Tool  | Calls  | Distinct files/patterns | Notable |
|-------|--------|-------------------------|---------|
| Edit  | 10,753 | 1,152 files             | median 4 edits/file, p90=19, MAX=301, 115 files edited >=20x |
| Read  | 11,775 | 1,992 files             | 4,216 partial (offset/limit) = 36%; ~7,559 whole-file (many re-reads) |
| Write | 1,198  | 1,057 files             | mostly one-shot creation |
| Grep  | 3,589  | 3,173 patterns          | 94% patterns UNIQUE (ad-hoc); + 3,852 grep-in-bash elsewhere |

## 1. Edit is dominated by repeated edits to the SAME hot files -> BATCHING opportunity
Top edited files (Edit calls):
- EntityRenderer.java 301, BlockRenderer.java 168, EntityModelLoader.java 159,
  ItemRenderer.java 133, EntityOverlayResolver.java 108, EntityGeometryKit.java 106,
  ModelEngine.java 105, PlayerRenderer.java 97 ... (all asset-renderer core classes)
- MEMORY.md 175 (memory churn), notes/timeline/*.md 137/84/79 (planning-doc churn)
115 files were edited >=20 times; 313 >=10 times. Many of these are sequential edits
within one turn -> each is a full round-trip + re-Read. Directly correlates with the
220 "has not been read yet" + 152 "String to replace not found" + 12 "Found 2 matches"
errors (stale/non-unique edit targets after prior edits).
RECOMMENDATION: a "batch edits to one file" discipline (plan all hunks, apply in as few
calls as possible / rewrite region once), and prefer editing then re-reading only the
changed region. Cuts Edit calls AND the ~384 stale-edit errors.

## 2. Read: 36% partial + heavy re-reads of the same files
11,775 reads over 1,992 files, 4,216 with offset/limit. Combined with 2,353 `head` +
1,888 `tail` in bash, file-slicing is a major activity. Re-reads after edits (invalidated
harness state) are a large chunk.
RECOMMENDATION: batching edits (item 1) removes forced re-reads; a project "hot files /
architecture map" in CLAUDE.md cuts exploratory reads of the same core classes.

## 3. Grep: almost entirely ad-hoc regex; ~300 are STRUCTURAL searches that belong in AST tools
3,589 Grep-tool calls, 3,173 distinct patterns (94% unique). Semantic buckets over the
patterns: "class " 157, "import " 100, "private/public " 48/146, "interface " 24,
"implements " 15, "extends " 11, "throw new" 2. So ~300+ searches are for Java structure
(declarations, imports, inheritance) that an AST tool (IntelliJ search_symbol / the
existing code-review-graph MCP: callers_of/callees_of/imports_of) resolves precisely in
ONE call. Plus 3,852 grep-in-bash. Yet IDE search_symbol was used ~1x.
RECOMMENDATION: make java-symbol-search / java-find-usages actually fire on
class/extends/implements/import/usage queries and route them to the graph MCP or IDE;
reserve regex grep for free-text. This is the single biggest "better grep/symbol search"
win (points 0 & 5).

## 4. Memory churn
MEMORY.md edited 175x (2nd most-edited file overall). Heavy rewrite cost every session.
Feeds the memory/context recommendation (mempalace or a leaner append-structured memory).

## Artifacts
edited_files_hist.txt, read_files_hist.txt, grep_patterns_hist.txt (full histograms in scratchpad).
