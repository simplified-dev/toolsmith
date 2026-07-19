#!/usr/bin/env bash
# DRAFT-locate-java.sh - resolve a Java type/file to its absolute path across every module in the
# Simplified container, in ONE call. Collapses the find(fail)->find(fail)->Glob loop that drives
# the "No such file / File does not exist" bucket when the module is unknown or the pkg root is
# heterogeneous (asset-renderer=lib/..., hypixel=api/..., persistence=dev/simplified/...).
#
# Install:  cp to ~/.claude/bin/locate-java.sh (or any PATH dir); chmod +x.
# Usage:    locate-java.sh <NameOrFragment> [--test] [--res]
#   <NameOrFragment>  bare class name (Item), partial path (renderer/Item), or file (Item.java).
#   --test            search src/test/java instead of src/main/java.
#   --res             search src/main/resources (for *.json data / config).
# Output:   absolute forward-slash paths, one per match (empty + exit 1 if none).
#
# Forward-slash safe: ROOT is written with '/', so results paste straight back into the Bash tool.
set -euo pipefail

ROOT="W:/Workspace/Java/Simplified"
sub="src/main/java"
for a in "$@"; do
    case "$a" in
        --test) sub="src/test/java" ;;
        --res)  sub="src/main/resources" ;;
    esac
done

# First non-flag argument is the query.
query=""
for a in "$@"; do
    case "$a" in
        --*) ;;
        *) query="$a"; break ;;
    esac
done
if [ -z "$query" ]; then
    echo "usage: locate-java.sh <NameOrFragment> [--test] [--res]" >&2
    exit 2
fi

# Normalize: strip a trailing .java so both 'Item' and 'Item.java' work; add .java for java subs.
base="${query%.java}"
if [ "$sub" = "src/main/resources" ]; then
    pattern="*${base}*"
else
    pattern="*${base}.java"
fi

# One find over all module <sub> trees; -path keeps the module/<sub> segment so cross-root
# duplicates (e.g. an Item.java in two modules) are both shown with full context.
matches="$(find "$ROOT" -type f -path "*/$sub/$pattern" 2>/dev/null | sort)"

if [ -z "$matches" ]; then
    echo "no match for '$query' under */$sub" >&2
    exit 1
fi
printf '%s\n' "$matches"
