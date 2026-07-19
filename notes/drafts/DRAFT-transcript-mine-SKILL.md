---
name: transcript-mine
description: Distill Claude Code JSONL transcripts (this project's past sessions) into compact ranked artifacts - tool-use frequency, the full shell-command corpus, Edit/Read/Write/Grep INPUT histograms, and the avoidable-error histogram. Auto-invoked when the user asks "what do I do a lot", "audit my token usage", "analyze my past sessions/history", "where do I waste tool calls", or before designing a workflow/skill that should be grounded in real usage. Runs ripgrep in the Bash tool shell (NOT a nested script).
auto_invoke: true
tags: [meta, transcripts, token-optimization, ripgrep, analysis]
---

# transcript-mine

Mine `~/.claude/projects/<encoded-prefix>*/**/*.jsonl` into small artifacts you can
actually read, instead of hand-rolling regex each time. This skill exists because
deriving these extractions live repeatedly costs ~15 tool calls of trial-and-error.

## Environment facts baked in (each cost real debugging)

1. **`rg` here is a bash FUNCTION** injected by Claude Code that shells out to
   `claude.exe` - there is no standalone `rg` binary on PATH. So these commands must
   run in the **Bash tool shell directly**, never as `bash some-script.sh` (a child
   shell does not inherit the function). `grep -oP` is NOT a substitute: this git-bash
   errors "supports only unibyte and UTF-8 locales" and chokes on the 100k+ char lines.
2. **The JSON-string capture MUST be `((?:\\.|[^"])*)`.** The intuitive
   `((?:[^"\\]|\\.)*)` silently matches NOTHING in ripgrep's regex engine.
3. **`file_path` is not the first input key** (Edit is `{"replace_all":...,"file_path":...}`),
   so capture it position-independently with `\{[^}]*?"file_path":"..."`.
4. **Mine with `rg`, never PowerShell `Select-String`** - the latter undercounts
   tool_use blocks on multi-MB single-line records (observed 4x undercount on Grep).

## Recipe (run in the Bash tool; set PREFIX + OUT first)

```bash
cd ~/.claude/projects
PREFIX=W--Workspace-Java-Simplified          # encoded project-dir prefix (covers all sub-projects)
OUT=/path/to/scratch/mine; mkdir -p "$OUT"
DIRS=$(ls -d "$PREFIX"* 2>/dev/null)
G=(-g '*.jsonl' -g '!*<CURRENT_SESSION_ID>*')   # exclude the meta-session you're running in
J='((?:\\.|[^"])*)'                              # the ONLY correct JSON-string capture

# tool-use frequency (reliable)
rg -oN --no-filename "${G[@]}" -r '$1' '"name":"([A-Za-z0-9_]+)","input":' $DIRS | sort | uniq -c | sort -rn > "$OUT/tool_freq.txt"
# full shell-command corpus + deduped shapes + verb histogram
rg -oN --no-filename "${G[@]}" -r '$1' "\"command\":\"$J\"" $DIRS > "$OUT/commands_raw.txt"
sort "$OUT/commands_raw.txt" | uniq -c | sort -rn > "$OUT/commands_dedup.txt"
# Edit / Read / Write file targets
for T in Edit Read Write; do
  rg -oN --no-filename "${G[@]}" -r '$1' "\"name\":\"$T\",\"input\":\{[^}]*?\"file_path\":\"$J\"" $DIRS | tr -s '\\' '/' | sort | uniq -c | sort -rn > "$OUT/${T}_files.txt"
done
# Grep-tool patterns (what gets searched)
rg -oN --no-filename "${G[@]}" -r '$1' "\"name\":\"Grep\",\"input\":\{\"pattern\":\"$J\"" $DIRS | sort | uniq -c | sort -rn > "$OUT/grep_patterns.txt"
# avoidable wrong-assumption failures (harness errors, not compile failures)
rg -oiN --no-filename "${G[@]}" '(File does not exist|has not been read yet|String to replace not found|Found [0-9]+ matches of the string|No such file or directory|command not found|InputValidationError)' $DIRS | sort | uniq -c | sort -rn > "$OUT/tool_errors.txt"
```

## Interpreting
- `tool_freq.txt` / `commands_dedup.txt`: what you do most and which command shapes are
  reinvented (candidates for a wrapper skill).
- `Edit_files.txt`: files edited many times = batch-edit candidates + stale-edit-error source.
- `grep_patterns.txt`: patterns matching `class /extends /implements /import ` should route
  to IDE/graph-MCP symbol search, not regex grep.
- `tool_errors.txt`: each line is an avoidable failed round-trip - the point-6 backlog.

## Note
Ship as a skill (this file), not a `.sh` - see environment fact #1. A future consolidated
Java/meta MCP server could expose this as one typed `mine_transcripts` tool.
