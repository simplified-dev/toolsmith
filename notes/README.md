# notes/ - token-optimization audit (provenance of toolsmith)

This directory is the record of the token-optimization audit that produced `toolsmith`.
It mined ~410 MB of past Claude sessions over the Simplified workspace to find where tool
tokens are wasted, then designed skills / CLAUDE.md rules / helpers to cut it. `toolsmith`
itself is the first productionized output (the deterministic tools consolidated into an MCP
server).

## Layout

- `FINAL-REPORT.md` - the full report across all 7 asks + a prioritized install checklist.
- `VERIFICATION.md` - the adversarial verification of every draft (0 blocker / 2 major / 10 minor;
  all majors were the incomplete-module-inventory issue, now fixed - see below).
- `optimize_workflow.js` - the multi-agent workflow script that generated the analysis.
- `analysis/` - the per-dimension analyses (`A1`-`A5`), `tool_io_findings.md`, the canonical
  `module-inventory.md`, and `import_blocks_sample.txt` (real-code evidence for the import order).
- `drafts/` - the proposed deliverables (see status table).
- `data/` - raw distilled evidence (command corpus, error histograms, ...). **Gitignored**: it
  contains verbatim excerpts from private sessions. Kept locally for reference, never committed.

## Draft status

| Draft | Status | Where it goes |
|-------|--------|---------------|
| `DRAFT-reorder_imports.py` | **SUPERSEDED** | -> `toolsmith/src/toolsmith/imports.py` (tested, 6 tests) |
| `DRAFT-jtally.py` | **SUPERSEDED** | -> `toolsmith/src/toolsmith/tally.py` |
| `DRAFT-gwverify.sh` | superseded (MCP) / standalone (shell) | -> `toolsmith` `gradle_verify`; the `.sh` still works as a direct helper |
| `DRAFT-simplified-tools-mcp-server.py`, `DRAFT-simplified-tools.mcp.json` | **SUPERSEDED** | became the whole `toolsmith` package. NOTE: the draft used the fastmcp **v2** API (`@mcp.tool(annotations=...)`), which raises `TypeError` on the installed **fastmcp 1.0**; toolsmith uses the correct `@mcp.tool()`. |
| `DRAFT-java-file-mover-SKILL.md` | PENDING INSTALL | -> `~/.claude/skills/java-file-mover/` + bundle a copy of `imports.py` as `reorder_imports.py` |
| `DRAFT-transcript-mine-SKILL.md` | PENDING INSTALL | -> `~/.claude/skills/transcript-mine/` |
| `DRAFT-assumptions-CLAUDE-md.md` | PENDING INSTALL | paste 3 sections into `~/.claude/CLAUDE.md` |
| `DRAFT-command-CLAUDE-md.md` | PENDING INSTALL | paste into `~/.claude/CLAUDE.md` + install helpers below |
| `DRAFT-gw.sh`, `DRAFT-modules.sh`, `DRAFT-locate-java.sh`, `DRAFT-recall-transcripts.sh` | PENDING INSTALL | `~/.claude/bin/` (the shell path; overlaps toolsmith's MCP path - pick one or run both) |
| `DRAFT-import-order.md` | RECORD (verified SOLID) | reference for the import layout |
| `DRAFT-skill-patches.md` | RECORD | per-skill fixes to apply to the existing `~/.claude/skills` |
| `DRAFT-mcp-memory-recommendations.md` | RECORD | the mempalace / MCP / memory decisions |

## Module inventory - the fixed major issue

The audit's one systemic defect was a half-complete module table duplicated across several
drafts (re-enabling the path-guessing it was meant to kill). Fixed by establishing **one**
canonical source and pointing everything at it:

- Machine-readable: `toolsmith/src/toolsmith/modules.py` (`ALIASES` + `PACKAGE_ROOTS`, regression-tested).
- Human-readable: `analysis/module-inventory.md` (all 34 buildable modules + verified package roots).

The counter-intuitive roots (the traps): `collections` -> `dev.simplified.collection` (singular),
`utils` -> `dev.simplified.util` (singular), `gson-extras` -> `dev.simplified.gson`,
`spring-framework` -> `dev.simplified.serverapi`, `discord4j-framework` -> `dev.simplified.discordapi`.

## Two delivery paths for the deterministic tools

The same operations exist as (1) the **toolsmith MCP server** (typed, auto-invocable, project-scoped)
and (2) **standalone shell helpers** (`gw`/`jtally`/`modules.sh`/`locate-java` in `~/.claude/bin`, for
direct Bash-tool use with no MCP). They are redundant on purpose - install whichever fits, or both.
The drafts' shell copies and toolsmith's Python are kept aligned via the one module inventory above.

## Shell-tool gotcha baked into every helper

`rg` in the Claude Bash tool is a shell **function** (it shells out to claude.exe), not a binary, so a
child `bash script.sh` cannot use it, and `grep -oP` errors on this locale. Every committed `.sh` here
uses plain `grep`/`sed`; the transcript miner ships as a skill (run in the Bash tool) rather than a
script for exactly this reason.
