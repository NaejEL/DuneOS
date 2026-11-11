#!/usr/bin/env bash
# Runs a headless software-factory cycle on an already approved spec.
# Usage: ci/factory.sh <spec-path> [--yolo]
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

spec=""
yolo=0
for arg in "$@"; do
    case "$arg" in
        --yolo)
            yolo=1
            ;;
        -h | --help)
            echo "Usage: ci/factory.sh <spec-path> [--yolo]"
            exit 0
            ;;
        -*)
            echo "Unknown option: $arg" >&2
            echo "Usage: ci/factory.sh <spec-path> [--yolo]" >&2
            exit 2
            ;;
        *)
            if [ -n "$spec" ]; then
                echo "A single spec path is expected (extra argument received: $arg)" >&2
                exit 2
            fi
            spec="$arg"
            ;;
    esac
done

if [ -z "$spec" ]; then
    echo "Path of the approved spec is missing." >&2
    echo "Usage: ci/factory.sh <spec-path> [--yolo]" >&2
    exit 2
fi

if [ ! -f "$spec" ]; then
    echo "Spec not found: $spec" >&2
    exit 1
fi

if ! grep -Eq "^[[:space:]]*(Status[[:space:]]*:[[:space:]]*APPROVED|Statut[[:space:]]*:[[:space:]]*APPROUVEE)" "$spec"; then
    echo "Spec not approved: '$spec' does not contain 'Status: APPROVED' (or the French 'Statut : APPROUVEE')." >&2
    echo "A CI cycle requires a spec validated by the Product Owner." >&2
    exit 1
fi

log_dir="$repo_dir/factory-logs"
mkdir -p "$log_dir"
log_file="$log_dir/factory-$(date +%Y%m%d-%H%M%S).json"

export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
cmd=(claude -p "/factory-run '$spec'" --output-format json --max-turns 100)

if [ "$yolo" -eq 1 ]; then
    cmd+=(--dangerously-skip-permissions)
else
    cmd+=(--permission-mode acceptEdits)
    cmd+=(--allowedTools "@ALLOWED@")
fi

echo "Spec    : $spec"
echo "Mode    : $([ "$yolo" -eq 1 ] && echo 'YOLO (permissions ignored)' || echo 'acceptEdits + allowlist')"
echo "Log     : $log_file"

set +e
"${cmd[@]}" | tee "$log_file"
status="${PIPESTATUS[0]}"
set -e

echo "claude exit: exit code $status"
exit "$status"
