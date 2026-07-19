export const meta = {
  name: 'token-optimization-audit',
  description: 'Analyze 410MB of Claude transcripts + skills + memory to design token-saving skills, CLAUDE.md rules, and tooling',
  phases: [
    { title: 'Analyze' },
    { title: 'Synthesize' },
  ],
}

const SCRATCH = 'C:/Users/BrianGraham/AppData/Local/Temp/claude/W--Workspace-Java-Simplified/c3960425-4101-4f68-b58b-66168e60336c/scratchpad'
const NOTES = SCRATCH + '/notes'

// Shampled facts already established by the orchestrator; agents must NOT re-derive these. (template-literal safe: no backticks, no dollar-brace)
const CONTEXT = [
  '=== ESTABLISHED CONTEXT (do not re-derive; build ON it) ===',
  'Environment: Windows 11. Primary CWD W:/Workspace/Java/Simplified (NOT a git repo at root; each family/module underneath is its own git repo). Claude drives work through git-bash via the Bash tool overwhelmingly (6973 Bash calls vs 31 PowerShell).',
  'Project family roots (each contains git repos/modules):',
  '  W:/Workspace/Java/Simplified/SkyBlock-Simplified (simplified-bot, simplified-server)',
  '  W:/Workspace/Java/Simplified/Simplified-Dev (modules: persistence, discord4j-framework, spring-framework, annotations, collections, gson-extras, image)',
  '  W:/Workspace/Java/Simplified/Simplified-Api (mojang, hypixel)',
  '  W:/Workspace/Java/Simplified/Minecraft-Library (asset-renderer [DOMINANT: 377MB of sessions], minecraft-text, nbt-factory, vanilla-reference-harness)',
  'User global CLAUDE.md: C:/Users/BrianGraham/.claude/CLAUDE.md  and  C:/Users/BrianGraham/.claude/workflow-orchestration.md',
  'Skills dir (16 user-authored skills): C:/Users/BrianGraham/.claude/skills',
  'Memory: C:/Users/BrianGraham/.claude/projects/W--Workspace-Java-Simplified/memory/ (MEMORY.md index + dedicated .md files)',
  'IntelliJ code style: BOTH .idea/codeStyles/codeStyleConfig.xml set PREFERRED_PROJECT_CODE_STYLE=Default with NO custom Project.xml IMPORT_LAYOUT_TABLE and NO .editorconfig anywhere. => The authoritative import order IS IntelliJ built-in Default scheme. Runtime source of truth = mcp__IntelliJ_IDE__reformat_file / Optimize-Imports. A no-IDE fallback must be derived EMPIRICALLY from real committed .java files.',
  'Tool-use histogram (all sessions): Bash 6973, Edit 6443, Read 4533, Write 922, Grep 892, TaskUpdate 787, TaskCreate 458, Agent 226, Glob 199, IntelliJ get_file_problems 148, ToolSearch 132, Workflow 55, IntelliJ rename_refactoring 32, PowerShell 31, reformat_file 2, search_symbol 1. (IDE symbol tools barely used despite skills routing to them.)',
  'Command-verb histogram (33627 shell commands): echo 4821, grep 3852 (ripgrep rg only 34), head 2353, tail 1888, gradlew ~2300+2287, git 1658 (status 687 / log 678 / commit 521 / diff 339 / mv 63), sed 1037, ls 1128, find 754, wc 633, sort 392, tree 352, awk 264, xargs 162, cp 209, mv 79. Chaining ampersand-ampersand 5036, pipe 5029.',
  'Tool-error histogram (avoidable wrong-assumption failures; this meta-session excluded): "No such file or directory" 492, "has not been read yet" 220 (Edit before Read), "String to replace not found" 152, "File does not exist" 120, "command not found" 34, InputValidationError 22, "Found 2 matches" 12.',
  'Ad-hoc throwaway-tool authoring events: 869. Examples: Claude repeatedly wrote /tmp/sortimports.py to reorder imports; inline python3 -c to parse build/test-results/test/*.xml into a test-pass/fail tally (many variants); jitpack build-status poll loops; cat > /tmp/pr-body.md heredocs.',
  'sortimports.py (Claude ad-hoc import reorderer, recovered): only alphabetizes each contiguous run of "import ...;" lines by full stripped line string. NOT IntelliJ-accurate: it interleaves "import static" with normal imports and never reorders across blank-line groups.',
  'The single most-repeated real command shape (hundreds of near-identical variants): cd "W:/.../asset-renderer" && ./gradlew compileJava [compileTestJava] -q 2>&1 | grep -vE "incubating|warning" | tail -N ; echo "EXIT: PIPESTATUS". Each hand-rewrites the cd prefix, the noise filter, tail -N (8/12/15/20/25/30/40), and a PIPESTATUS echo. Also a recurring test-XML-tally (grep+awk or python over build/test-results/test/*.xml) and git status+diff --stat combos.',
  'Existing skills: java-bulk-rename (routes renames to IntelliJ rename_refactoring, sed fallback, then reformat_file + gradle-verify-gate), javadoc-normalize (real python tool: SAFE_FIXES + FQN auto-import + _inject_imports which groups imports by top-level package and routes java/javax to a java-group, sorts within group), gradle-verify-gate (advises module-scoped ./gradlew but ships NO reusable noise-stripping wrapper command), java-symbol-search, java-find-usages, java-import-audit, java-modifier-audit, java-record-audit, java-exception-class-gen, context-engineering, pattern-recognition. debug-issue.md and explore-codebase.md reference an EXISTING "code-review-graph" knowledge-graph MCP (semantic_search_nodes, query_graph with callers_of/callees_of/imports_of, get_flow, get_impact_radius, get_architecture_overview) - a graph-based code-nav MCP the user already has.',
  'mempalace/mempalace: 57.5k-star MIT Python, LOCAL-FIRST memory (ChromaDB bundled, no API key, .mcp.json just launches local binary "mempalace-mcp" with no remote endpoint). Ships a Claude Code plugin: hooks Stop (save every 15 msgs), SessionEnd (final save), PreCompact (save before context compaction) + a 36-tool MCP server. It IS auto-invocable by the agent (hooks + MCP), needs no manual human step - which matches the user hard requirement.',
  'Distilled artifacts in scratchpad (' + SCRATCH + '): tool_freq.txt, command_keywords.txt, bash_commands_dedup.txt (3500 ranked shapes; IGNORE lines 1-3 = hook status noise "Protecting secrets..."/"Blocking dangerous commands..."), bash_commands_raw.txt (33627 lines - use Grep/head, never full Read), corrections.txt (294 self-corrections), tool_errors.txt (1043) + tool_errors_hist.txt, file_ops.txt (246 move ops), adhoc_scripts.txt (869 throwaway-tool events).',
  'GOAL: maximize FUTURE token-consumption savings in the user Java projects. Deliverables must be concrete and installable: new/patched SKILL.md files, exact CLAUDE.md text to add, and helper scripts. Follow the user CLAUDE.md conventions in anything you draft (javadoc style, exceptions, control-flow braces, git mv rule, etc.).',
].join('\n')

const ANALYSIS_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['area', 'summary', 'findings', 'deliverables', 'tokenSaveEstimate'],
  properties: {
    area: { type: 'string' },
    summary: { type: 'string' },
    findings: {
      type: 'array', maxItems: 14,
      items: {
        type: 'object', additionalProperties: false,
        required: ['title', 'evidence', 'recommendation', 'impact'],
        properties: {
          title: { type: 'string' },
          evidence: { type: 'string' },
          recommendation: { type: 'string' },
          impact: { type: 'string', enum: ['high', 'medium', 'low'] },
        },
      },
    },
    deliverables: {
      type: 'array', maxItems: 10,
      items: {
        type: 'object', additionalProperties: false,
        required: ['path', 'what'],
        properties: { path: { type: 'string' }, what: { type: 'string' } },
      },
    },
    tokenSaveEstimate: { type: 'string' },
  },
}

const outRule = (name) =>
  '\n\nOUTPUT RULES: Write a dense analysis to ' + NOTES + '/' + name + '.md (author incrementally: Write a skeleton with headings first, then fill each section with a separate Edit, <=120 new lines per call - never one giant Write). Write each concrete draft deliverable to its own file under ' + SCRATCH + ' with a DRAFT- prefix (e.g. DRAFT-... .md / .py / .sh). Drafts must be complete and ready to install, and must obey the user CLAUDE.md conventions. Then RETURN the JSON schema summary (your findings[].evidence must cite concrete numbers/paths/command-shapes, not vague claims). Do not restate file contents in your return value - reference paths.'

phase('Analyze')
log('Fanning out 5 analysis agents over the distilled transcript corpus + skills + memory')

const analyses = await parallel([
  // A1 - command/tool-call chaining & throwaway-tool patterns (points 0, 5)
  () => agent(
    CONTEXT +
    '\n\n=== YOUR TASK (A1: command & tool-call token waste) ===\n' +
    'Read ' + SCRATCH + '/command_keywords.txt, ' + SCRATCH + '/bash_commands_dedup.txt (ranked; skip lines 1-3), ' + SCRATCH + '/adhoc_scripts.txt, and Grep ' + SCRATCH + '/bash_commands_raw.txt for specific shapes. Identify the highest-volume REPEATED / REINVENTED command shapes that waste output tokens, and design canonical replacements that collapse many tool calls into one. Cover at minimum:\n' +
    '1. The gradle compile/test noise-filter+tail+PIPESTATUS incantation reinvented hundreds of times. Design ONE reusable wrapper (a committed helper script and/or shell function, e.g. a "gw" wrapper: cd module, run gradle quietly, strip the known-noise lines [incubating/warning], tail, print a clean EXIT + a test-XML tally) that the user can invoke as a single short command. Provide the actual script.\n' +
    '2. The repeated per-command cd "W:/.../module" && ... prefix - propose a fix (module path vars / a CLAUDE.md note that Bash cwd persists / a helper). \n' +
    '3. The build/test-results/*.xml test-tally reinvented inline in python/awk many times - provide ONE canonical test-tally script.\n' +
    '4. echo used 4821x and grep 3852x vs ripgrep 34x, head/tail slicing files instead of Read offset/limit - quantify and give guidance.\n' +
    '5. The 869 throwaway-tool authoring events - which recurring ones deserve a permanent committed helper vs a skill.\n' +
    'For each: estimate the token waste and the saving. Prefer solutions that are ONE tool call. ' +
    'Deliverables: DRAFT-gradle-wrapper.sh (or .md describing it), DRAFT-test-tally script, and a DRAFT-command-CLAUDE-md.md snippet of exact lines to add to CLAUDE.md.' +
    outRule('A1-command-patterns'),
    { label: 'A1:commands', phase: 'Analyze', schema: ANALYSIS_SCHEMA }
  ),

  // A2 - wrong-assumption / self-correction reduction (point 6)
  () => agent(
    CONTEXT +
    '\n\n=== YOUR TASK (A2: shrink wrong assumptions, N calls -> 1) ===\n' +
    'Read ' + SCRATCH + '/tool_errors_hist.txt, ' + SCRATCH + '/tool_errors.txt, and ' + SCRATCH + '/corrections.txt. The tool-error histogram shows ~1000 avoidable failed tool calls. Categorize the wrong-assumption failure modes and design MINIMAL, mechanical countermeasures - each should turn N tool calls into ~1. Focus:\n' +
    '1. "No such file or directory" 492 + "File does not exist" 120: Claude guesses paths. Countermeasure ideas: a CLAUDE.md "Project Map" section listing every module root + its build dir + resource roots + memory path (so paths are looked up, not guessed); a rule to Glob/verify before Read; a helper. Draft the exact Project Map text from the roots in CONTEXT.\n' +
    '2. "has not been read yet" 220: Edit-before-Read. Propose a rule/pattern (e.g. always Read (or Grep with context) the target region before Edit; or prefer a single Read+Edit ordering) and note the read-before-edit gate interplay with IntelliJ MCP.\n' +
    '3. "String to replace not found" 152 + "Found 2 matches" 12: stale/non-unique old_string. Propose guidance (Read the exact current bytes first; include enough context to be unique; prefer replace_all when intended).\n' +
    '4. Scan corrections.txt for recurring semantic wrong-assumptions (e.g. wrong module/path, wrong Hibernate property, README-out-of-date) that a durable CLAUDE.md/memory fact would have prevented; list the top ones.\n' +
    'Prioritize by frequency. Deliverable: DRAFT-assumptions-CLAUDE-md.md with the exact CLAUDE.md additions (Project Map + read/edit discipline rules), phrased tightly (these lines cost context every session - keep them dense).' +
    outRule('A2-wrong-assumptions'),
    { label: 'A2:assumptions', phase: 'Analyze', schema: ANALYSIS_SCHEMA }
  ),

  // A3 - existing-skill gap/edge-case audit (point 1)
  () => agent(
    CONTEXT +
    '\n\n=== YOUR TASK (A3: audit & upgrade existing skills) ===\n' +
    'Glob and Read every file under C:/Users/BrianGraham/.claude/skills (16 skills). For EACH user skill assess: (a) does its description/auto-invoke actually fire on the real triggers seen in the corpus? (b) edge cases / features it misses that forced Claude to fall back to manual sed/grep/Edit (cross-check bash_commands_dedup.txt and adhoc_scripts.txt); (c) correctness bugs; (d) overlap/redundancy with sibling skills; (e) does it leave the user CLAUDE.md conventions half-done. Concrete known leads to verify and expand:\n' +
    '- gradle-verify-gate ships no actual noise-stripping wrapper command (coordinate with A1).\n' +
    '- javadoc-normalize _inject_imports grouping may NOT match IntelliJ Default order (coordinate with A4) - audit whether its group routing/sort is correct, and whether it should expose a standalone reorder entry point.\n' +
    '- java-bulk-rename used only 32x while sed ran 1037x - is the skill under-triggering? Should it own more of the "move/rename" space (coordinate with A4)?\n' +
    '- java-symbol-search / java-find-usages route to IDE tools that were used ~1x - is the IDE-attached assumption realistic, and are the Grep fallbacks strong enough?\n' +
    'Deliverable: DRAFT-skill-patches.md with a per-skill section giving the EXACT edits/additions (diff-style or full replacement blocks) needed to close each gap. Rank skills by impact.' +
    outRule('A3-skill-audit'),
    { label: 'A3:skill-audit', phase: 'Analyze', schema: ANALYSIS_SCHEMA }
  ),

  // A4 - THE file-mover skill + IntelliJ import-order derivation (point 2)
  () => agent(
    CONTEXT +
    '\n\n=== YOUR TASK (A4: design the java-file-mover skill + faithful import reorder) ===\n' +
    'This is the flagship deliverable. Two parts:\n' +
    'PART 1 - Derive IntelliJ Default import order EMPIRICALLY. Use Glob to find real committed .java files across the modules (sample >=40 spanning different modules, especially files that have BOTH normal and "import static" lines and files with several top-level package prefixes java./javax./org./com./dev./net./lib.). Read their import blocks. Determine precisely: is it one alphabetical block or multiple blank-line-separated groups? Where do "import static" lines go (top or bottom, own group?)? Is ordering case-sensitive/ASCII? Are there on-demand wildcards (and the class-count threshold) or are wildcards absent? Cross-check against the user CLAUDE.md and the java-import-audit skill. Then compare that TRUE order against (a) javadoc-normalize _inject_imports and (b) the naive sortimports.py - state exactly where each diverges. Write DRAFT-import-order.md documenting the derived order with cited example files, AND write DRAFT-reorder_imports.py: a robust, IDE-independent reorderer that reproduces the IntelliJ Default order faithfully (handles static grouping, blank-line groups, wildcards, CRLF/LF preservation, package-statement/leading-comment safety). It must be callable on a list of files and be idempotent.\n' +
    'PART 2 - Design DRAFT-java-file-mover-SKILL.md, a skill that covers ~100% of "move/rename/relocate a Java file" cases:\n' +
    '  - Same-package class rename (no dir change): route to mcp__IntelliJ_IDE__rename_refactoring (or instruct direct rename if IDE absent).\n' +
    '  - Cross-package / cross-directory MOVE: (1) check git status of the SPECIFIC repo the file lives in - git mv if tracked & clean-enough, else plain mv/Write; recreation is the LAST resort and only when not git-tracked. (2) rewrite the package statement. (3) find every import of the moved type across the owning repo (and dependent repos) and update it - specify the exact search (fully-qualified type, on-demand wildcard of old package, same-package references that now need a new import). (4) call the import reorder (reorder_imports.py or IntelliJ Optimize-Imports) then reformat_file, then invoke gradle-verify-gate. Reuse java-import-audit / javadoc-normalize as sub-steps rather than duplicating.\n' +
    '  - Move to a DIFFERENT module: also handle build.gradle(.kts) dependency direction and the read-before-edit re-Read rule.\n' +
    '  - Decision tree + failure/rollback handling. Cite which existing skills it delegates to. Make auto-invoke triggers match how the user actually phrases it ("move X to package Y", "relocate", "put this in module Z").\n' +
    'Deliverables: DRAFT-java-file-mover-SKILL.md, DRAFT-reorder_imports.py, DRAFT-import-order.md.' +
    outRule('A4-file-mover'),
    { label: 'A4:file-mover', phase: 'Analyze', schema: ANALYSIS_SCHEMA }
  ),

  // A5 - MCP-server conversion + memory/context + mempalace + graph-MCP (points 3, 4, 0, 7)
  () => agent(
    CONTEXT +
    '\n\n=== YOUR TASK (A5: skills-as-MCP, memory/context, mempalace, graph-nav) ===\n' +
    'Four assessments, each with a recommendation the user can act on:\n' +
    '1. SKILLS-AS-MCP (point 4): Read the plugin author guides at C:/Users/BrianGraham/.claude/plugins/marketplaces/claude-plugins-official/plugins/mcp-server-dev/skills (build-mcp-server, build-mcp-app, build-mcpb) and plugin-dev/skills/mcp-integration. Assess: is converting the user Java skills (java-bulk-rename, javadoc-normalize, the auditors, the new file-mover) into a single local MCP server EASIER for Claude to invoke than skills, more self-contained than polluting the skills dir, and easy to build? Weigh: skills = prompt-routing + on-demand load (cheap context, model must choose to invoke); MCP tool = always-listed schema (context cost per tool) but deterministic typed invocation. Recommend which skills (if any) should become MCP tools vs stay skills vs become plain committed scripts. Note the javadoc-normalize/reorder python could be exposed as MCP tools. Give a concrete minimal architecture (one stdio server, tool list) and an effort estimate.\n' +
    '2. MEMPALACE (point 3): Given the CONTEXT facts (local-first, MIT, hooks+MCP auto-invoke, no manual step), judge fit for THIS setup and its hard requirement (AI-auto-invocable only). Compare against the user existing file-based memory (MEMORY.md + dedicated files) - does it add value (verbatim semantic recall across 410MB of past sessions) or duplicate? Flag the residual safety unknowns (the mempalace-mcp binary internals were not line-audited) and give a low-risk trial recommendation (what to enable, what to watch). Keep it decision-oriented.\n' +
    '3. MEMORY/CONTEXT (points 0,7): Read C:/Users/BrianGraham/.claude/projects/W--Workspace-Java-Simplified/memory/MEMORY.md and the context-engineering skill. The architecture memory file is 146KB. Recommend concrete improvements: memory hygiene, what belongs in CLAUDE.md vs memory, and whether an auto-memory-recall mechanism (mempalace or a lighter grep-index over past transcripts) would cut re-derivation.\n' +
    '4. GRAPH-NAV MCP (points 0,5): The user already has a "code-review-graph" MCP (per debug-issue.md/explore-codebase.md) offering callers_of/callees_of/imports_of/get_flow/get_impact_radius. Given search_symbol/find-usages IDE tools are barely used, assess whether routing symbol-search/find-usages/impact analysis through this graph MCP would beat grep (3852 greps!) for token cost, and whether the java-symbol-search/java-find-usages skills should be rewired to it.\n' +
    'Deliverable: DRAFT-mcp-memory-recommendations.md with the four recommendations, each with a verdict (adopt / trial / skip) and the concrete first step.' +
    outRule('A5-mcp-memory'),
    { label: 'A5:mcp-memory', phase: 'Analyze', schema: ANALYSIS_SCHEMA }
  ),
])

const ok = analyses.filter(Boolean)
log('Analyze complete: ' + ok.length + '/5 agents returned. Synthesizing report + verifying drafts.')

phase('Synthesize')

// Build a compact digest of the analysis returns to seed the report + verify prompts.
const digest = ok.map(a => '### ' + (a && a.area ? a.area : 'unknown') + '\n' +
  (a && a.summary ? a.summary : '') + '\n' +
  'Findings: ' + ((a && a.findings) || []).map(f => '[' + f.impact + '] ' + f.title).join(' | ') + '\n' +
  'Deliverables: ' + ((a && a.deliverables) || []).map(d => d.path).join(', ') + '\n' +
  'Est. saving: ' + (a && a.tokenSaveEstimate ? a.tokenSaveEstimate : 'n/a')
).join('\n\n')

const REPORT_PATH = SCRATCH + '/FINAL-REPORT.md'

const results = await parallel([
  // Report writer (incremental authoring)
  () => agent(
    CONTEXT +
    '\n\n=== SYNTHESIS: executive report ===\n' +
    'You are writing the single deliverable report the user will read. Inputs: the analysis notes in ' + NOTES + '/*.md and the DRAFT-*.md/.py/.sh files in ' + SCRATCH + ' (Read the ones you cite). Analysis digest:\n\n' + digest +
    '\n\nWrite ' + REPORT_PATH + ' authored INCREMENTALLY (skeleton with all headings first, then one section per Edit, <=120 new lines each). Structure it around the user 7 numbered asks: (0) overall token-optimization thesis; (1) existing-skill upgrades; (2) the java-file-mover skill + faithful import reorder; (3) mempalace verdict; (4) skills-as-MCP verdict; (5) sed/grep/bash + tool-call chaining wins; (6) wrong-assumption countermeasures (lead with the ~1000-error histogram); (7) cross-cutting extrapolations (memory/context, graph-nav). For each: the finding (with concrete numbers), the recommendation, the exact deliverable file to install, and a rough token-saving. End with a PRIORITIZED INSTALL CHECKLIST (highest ROI first) mapping each item to its DRAFT file and where it installs (which CLAUDE.md / skills path). Be concrete and honest about uncertainty. RETURN only the string "' + REPORT_PATH + '".',
    { label: 'S1:report', phase: 'Synthesize' }
  ),

  // Single-pass verifier of the drafts
  () => agent(
    CONTEXT +
    '\n\n=== SYNTHESIS: adversarial verification (single pass) ===\n' +
    'Read every DRAFT-*.md/.py/.sh in ' + SCRATCH + ' and the notes in ' + NOTES + '. Verify each draft is CORRECT and installable, not just plausible. Check specifically: (a) the reorder_imports.py + DRAFT-import-order.md actually match IntelliJ Default order and the user codebase samples - spot-check against 2-3 real .java files; flag any divergence from the empirical order. (b) the file-mover skill honors the user CLAUDE.md git mv rule, read-before-edit rule, and does not delete git-tracked files. (c) any CLAUDE.md additions are dense (they cost context every session) and do not contradict existing CLAUDE.md rules. (d) proposed shell wrappers are correct (PIPESTATUS capture, noise filters do not swallow real errors, CRLF safety). (e) no fabricated tool names / MCP capabilities. Report issues most-severe first. RETURN the JSON schema.',
    { label: 'S2:verify', phase: 'Synthesize', schema: {
      type: 'object', additionalProperties: false,
      required: ['verdict', 'issues'],
      properties: {
        verdict: { type: 'string' },
        issues: {
          type: 'array', maxItems: 24,
          items: {
            type: 'object', additionalProperties: false,
            required: ['file', 'problem', 'severity', 'fix'],
            properties: {
              file: { type: 'string' },
              problem: { type: 'string' },
              severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
              fix: { type: 'string' },
            },
          },
        },
      },
    } }
  ),
])

return {
  analyses: ok.map(a => ({ area: a.area, deliverables: (a.deliverables || []).map(d => d.path), save: a.tokenSaveEstimate })),
  reportPath: results[0],
  verification: results[1],
}
