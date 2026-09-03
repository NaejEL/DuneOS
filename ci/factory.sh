#!/usr/bin/env bash
# Headless CI cycle of the DuneOS software factory.
# Usage: ci/factory.sh <specs/SPEC-*.md> [--yolo]
set -euo pipefail

usage() {
    echo "Usage: $0 <approved-spec-path> [--yolo]" >&2
    exit 2
}

SPEC=""
YOLO=0
for arg in "$@"; do
    case "$arg" in
        --yolo) YOLO=1 ;;
        -*) echo "Unknown option: $arg" >&2; usage ;;
        *) [ -n "$SPEC" ] && usage; SPEC="$arg" ;;
    esac
done
[ -n "$SPEC" ] || usage

if [ ! -f "$SPEC" ]; then
    echo "Error: spec not found: $SPEC" >&2
    exit 1
fi
if ! grep -q '^Status: APPROVED$' "$SPEC"; then
    echo "Error: $SPEC is not approved (line 'Status: APPROVED' missing)." >&2
    echo "The human gate happens in an interactive session: /factory-run \"<requirement>\"" >&2
    exit 1
fi

mkdir -p factory-logs
TS="$(date +%Y%m%d-%H%M%S)"
LOG="factory-logs/factory-${TS}.json"

ARGS=(-p "/factory-run '${SPEC}'" --output-format json --max-turns 100)
if [ "$YOLO" -eq 1 ]; then
    ARGS+=(--dangerously-skip-permissions)
else
    ARGS+=(--permission-mode acceptEdits
           --allowedTools
           "Read" "Glob" "Grep" "Write" "Edit"
           "Bash(git *)" "Bash(python *)" "Bash(idf.py *)"
           "PowerShell(git *)" "PowerShell(python *)" "PowerShell(idf.py *)")
fi

set +e
claude "${ARGS[@]}" > "$LOG" 2>&1
RC=$?
set -e

echo "Log: $LOG (claude exit code: $RC)"
exit "$RC"
