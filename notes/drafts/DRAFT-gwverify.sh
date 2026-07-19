#!/usr/bin/env bash
# gwverify.sh - canonical Java verification gate with gradle-noise stripping.
#
# Replaces the 240 hand-rewritten variants of:
#   cd "W:/.../module" && ./gradlew compileJava test -q 2>&1 \
#     | grep -vE "incubating|warning" | tail -N ; echo "EXIT: ${PIPESTATUS[0]}"
#
# Captures the TRUE gradle exit code (writes to a temp log, then filters the
# log - never relies on fragile ${PIPESTATUS} through a grep|tail pipeline),
# strips the standard noise, surfaces only the first N signal lines, and prints
# a stable machine-greppable trailer:  GATE: PASS rc=0   /   GATE: FAIL rc=1
#
# Usage:
#   gwverify.sh [-d DIR] [-n LINES] [-m MODULE] [-c] [TASK ...]
#
#   -d DIR     module or repo dir to run in (default: current dir). The script
#              walks up from here to find the nearest gradlew.
#   -m MODULE  gradle module path (e.g. asset-renderer). When set, tasks are
#              scoped as :MODULE:TASK. Omit for a root-level run.
#   -n LINES   max signal lines to print (default 25).
#   -c         compile-only: default tasks become "compileJava compileTestJava".
#   TASK ...   gradle tasks (default: "compileJava test", or the -c set).
#
# Exit code mirrors gradle's: 0 pass, non-zero fail. Stdout ends with the
# GATE: trailer so a caller can `grep '^GATE:'` for a verdict.
set -u

DIR="."
LINES=25
MODULE=""
COMPILE_ONLY=0

while getopts "d:n:m:c" opt; do
  case "$opt" in
    d) DIR="$OPTARG" ;;
    n) LINES="$OPTARG" ;;
    m) MODULE="$OPTARG" ;;
    c) COMPILE_ONLY=1 ;;
    *) echo "usage: gwverify.sh [-d DIR] [-n LINES] [-m MODULE] [-c] [TASK ...]" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

# Default task set.
if [ "$#" -gt 0 ]; then
  TASKS=("$@")
elif [ "$COMPILE_ONLY" -eq 1 ]; then
  TASKS=(compileJava compileTestJava)
else
  TASKS=(compileJava test)
fi

# Scope tasks to a module when -m given.
SCOPED=()
for t in "${TASKS[@]}"; do
  if [ -n "$MODULE" ]; then
    SCOPED+=(":${MODULE}:${t}")
  else
    SCOPED+=("$t")
  fi
done

# Locate gradlew by walking up from DIR.
find_gradlew() {
  local d
  d="$(cd "$1" 2>/dev/null && pwd)" || return 1
  while [ -n "$d" ]; do
    if [ -x "$d/gradlew" ] || [ -f "$d/gradlew" ]; then
      printf '%s\n' "$d"
      return 0
    fi
    [ "$d" = "/" ] && break
    d="$(dirname "$d")"
  done
  return 1
}

ROOT="$(find_gradlew "$DIR")" || { echo "gwverify: no gradlew found upward from '$DIR'" >&2; exit 2; }

LOG="$(mktemp)"
trap 'rm -f "$LOG"' EXIT

# Run gradle; capture the real rc. Do NOT pipe here - piping loses the code.
( cd "$ROOT" && ./gradlew "${SCOPED[@]}" -q ) >"$LOG" 2>&1
RC=$?

# Noise the operator stripped every time.
NOISE='incubating|Deprecated Gradle features|will fail with an error in Gradle|^warning:|^Note:|^> Task |^BUILD |Daemon will be stopped'

# Signal patterns worth surfacing first (compile errors, test failures).
SIGNAL='error:|\.java:[0-9]+|FAILED|FAILURE:|Compilation failed|> There were failing tests|Test .* FAILED|^Caused by:|Exception'

echo "gwverify: ${ROOT}  tasks: ${SCOPED[*]}"

# Prefer signal lines; if none, show the de-noised tail so a caller still sees
# context on an opaque failure.
SIG_OUT="$(grep -nE "$SIGNAL" "$LOG" | grep -vE "$NOISE" | head -n "$LINES" || true)"
if [ -n "$SIG_OUT" ]; then
  printf '%s\n' "$SIG_OUT"
else
  grep -vE "$NOISE" "$LOG" | tail -n "$LINES"
fi

if [ "$RC" -eq 0 ]; then
  echo "GATE: PASS rc=0"
else
  echo "GATE: FAIL rc=${RC}"
fi
exit "$RC"
