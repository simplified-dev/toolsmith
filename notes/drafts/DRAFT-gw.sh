#!/usr/bin/env bash
# gw - gradle wrapper for the Simplified modules.
#
# Collapses the ~1500 reinvented incantations of the form
#   cd "W:/.../asset-renderer" && ./gradlew compileJava -q 2>&1 \
#     | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS[0]}"
# into one short, cwd-independent command with a fixed compact output shape.
#
# Install: place at ~/.claude/bin/gw (chmod +x), alongside modules.sh and jtally,
# and put ~/.claude/bin on PATH (see DRAFT-command-CLAUDE-md.md for the profile line).
#
# Usage:
#   gw ar compileJava                    # asset-renderer compile
#   gw ar compileJava compileTestJava    # multiple tasks
#   gw d4j test                          # test + automatic XML tally
#   gw ar test --tail 25 --fails 20      # widen error tail / failing-test list
#   gw . build                           # use the current directory (has a gradlew)
#
# Output contract (stable, so re-runs never re-import a different shape):
#   success ->  "BUILD SUCCESSFUL in 3s  (ar: compileJava)  EXIT 0"   (nothing else)
#   failure ->  "---- ar: test (errors only) ----" + only error/FAILED lines + "EXIT <n>"
#   test task -> the jtally block is appended automatically.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$HERE/modules.sh"

TAIL=40
FAILS=15
ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --tail)  TAIL="$2";  shift 2 ;;
        --fails) FAILS="$2"; shift 2 ;;
        *)       ARGS+=("$1"); shift ;;
    esac
done
set -- "${ARGS[@]}"

if [ $# -lt 1 ]; then
    echo "usage: gw <module-alias|.> <gradle-task...> [--tail N] [--fails N]" >&2
    echo "aliases: $(simplified_module_aliases)" >&2
    exit 2
fi

ALIAS="$1"; shift
if [ "$ALIAS" = "." ]; then
    DIR="$PWD"
else
    DIR="$(simplified_module_dir "$ALIAS")"
fi
if [ -z "$DIR" ] || [ ! -d "$DIR" ]; then
    echo "gw: unknown module '$ALIAS' (aliases: $(simplified_module_aliases))" >&2
    exit 2
fi
if [ ! -f "$DIR/gradlew" ]; then
    echo "gw: no gradlew in $DIR" >&2
    exit 2
fi

TASKS=("$@")
[ ${#TASKS[@]} -eq 0 ] && TASKS=(compileJava)

# One canonical noise filter (the single source of truth that replaces the ~8
# drifting hand-spelled variants: incubating / incubat / incubating module / ...).
NOISE='incubating|^warning:|^[0-9]+ warning|WARNING: Using|^Note:|Daemon|^> Task |^> Configure|Welcome to Gradle|Starting a Gradle|Deprecated Gradle features|To honour|You can use|^$'

LOG="$(mktemp)"
( cd "$DIR" && ./gradlew "${TASKS[@]}" --console=plain ) >"$LOG" 2>&1
CODE=$?

if [ "$CODE" -eq 0 ]; then
    STATUS="$(grep -E 'BUILD SUCCESSFUL' "$LOG" | tail -1)"
    echo "${STATUS:-BUILD SUCCESSFUL}  (${ALIAS}: ${TASKS[*]})  EXIT 0"
else
    echo "---- ${ALIAS}: ${TASKS[*]}  (errors only) ----"
    grep -nE 'error:|\.java:[0-9]+:|[[:space:]]FAILED|BUILD FAILED|Caused by:|Exception:' "$LOG" \
        | grep -vE "$NOISE" \
        | tail -"$TAIL"
    echo "EXIT ${CODE}"
fi
rm -f "$LOG"

# Auto-tally when a test-shaped task ran (absorbs the ===TALLY=== step the
# discord4j sessions appended by hand).
for t in "${TASKS[@]}"; do
    case "$t" in
        test|slowTest|check|*[Tt]est)
            python3 "$HERE/jtally.py" "$DIR" --fails "$FAILS"
            break ;;
    esac
done

exit "$CODE"
