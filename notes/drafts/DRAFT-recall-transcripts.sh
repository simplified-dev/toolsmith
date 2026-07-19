#!/usr/bin/env bash
# recall-transcripts.sh - light, on-demand recall over past Claude session transcripts.
#
# The zero-always-on-tokens alternative to a semantic-memory MCP: it greps the
# session JSONL logs already on disk (no new binary, no embedding step, same corpus a
# memory MCP would index) and prints the matching turns with their source file so you
# can recall "what command did I use", "how did I fix X", "which file held Y" without
# re-deriving it. Lexical recall - grep-strength queries. For fuzzy semantic recall,
# escalate to the mempalace trial.
#
# Usage:
#   recall-transcripts.sh "gradle noise filter PIPESTATUS"
#   recall-transcripts.sh -n 40 "hazelcast configUri"
#   recall-transcripts.sh -r asset-renderer "reformat_file"   # restrict to one project
#
# Invoke on demand (e.g. from a tiny `transcript-recall` skill), NOT on every turn.
set -euo pipefail

CLAUDE_PROJECTS="${CLAUDE_PROJECTS:-$HOME/.claude/projects}"
LIMIT=25
PROJECT_FILTER=""

while getopts "n:r:h" opt; do
  case "$opt" in
    n) LIMIT="$OPTARG" ;;
    r) PROJECT_FILTER="$OPTARG" ;;
    h) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: $0 [-n LIMIT] [-r PROJECT_SUBSTR] QUERY" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

if [[ $# -eq 0 ]]; then
  echo "usage: $0 [-n LIMIT] [-r PROJECT_SUBSTR] QUERY" >&2
  exit 2
fi
QUERY="$*"

# NOTE: `rg` in the Claude Bash tool is a shell FUNCTION (shells out to claude.exe), not a
# binary - a child script like this cannot inherit it, so `command -v rg` fails and we use
# grep. That is correct here: `grep -oP` errors on this locale, plain grep is fine. The rg
# branch only fires if a real ripgrep binary is ever installed on PATH.
if command -v rg >/dev/null 2>&1; then
  SEARCH=(rg -i --no-heading -N --max-count 3)
else
  SEARCH=(grep -i -m 3 -H -n)
fi

SCOPE="$CLAUDE_PROJECTS"
[[ -n "$PROJECT_FILTER" ]] && SCOPE="$CLAUDE_PROJECTS"/*"$PROJECT_FILTER"*

echo "# recall: '$QUERY'  (top $LIMIT hits, newest sessions first)"
echo

# Newest JSONL first so recent sessions win; cap total hits at LIMIT.
found=0
while IFS= read -r jsonl; do
  [[ $found -ge $LIMIT ]] && break
  while IFS= read -r line; do
    [[ $found -ge $LIMIT ]] && break
    # Trim each hit to a readable single-line excerpt around the match.
    excerpt="$(printf '%s' "$line" | tr -d '\r' | cut -c1-300)"
    printf '%s\n    %s\n\n' "$(basename "$(dirname "$jsonl")")/$(basename "$jsonl")" "$excerpt"
    found=$((found + 1))
  done < <("${SEARCH[@]}" -- "$QUERY" "$jsonl" 2>/dev/null || true)
done < <(find $SCOPE -name '*.jsonl' -printf '%T@ %p\n' 2>/dev/null | sort -rn | cut -d' ' -f2-)

echo "# $found hit(s). Refine with more words or -r PROJECT to narrow."
