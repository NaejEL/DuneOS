#!/usr/bin/env bash
# Runs a headless AI-lab cycle on an already pre-registered protocol.
# Usage: ci/lab.sh <protocol-path> [--yolo]
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

protocol=""
yolo=0
for arg in "$@"; do
    case "$arg" in
        --yolo)
            yolo=1
            ;;
        -h | --help)
            echo "Usage: ci/lab.sh <protocol-path> [--yolo]"
            exit 0
            ;;
        -*)
            echo "Unknown option: $arg" >&2
            echo "Usage: ci/lab.sh <protocol-path> [--yolo]" >&2
            exit 2
            ;;
        *)
            if [ -n "$protocol" ]; then
                echo "A single protocol path is expected (extra argument received: $arg)" >&2
                exit 2
            fi
            protocol="$arg"
            ;;
    esac
done

if [ -z "$protocol" ]; then
    echo "Path of the pre-registered protocol is missing." >&2
    echo "Usage: ci/lab.sh <protocol-path> [--yolo]" >&2
    exit 2
fi

# A relative path is read from the current folder when it exists there, else from the repository root;
# it is made absolute because claude runs from the root.
case "$protocol" in
    /*) ;;
    *) if [ -f "$protocol" ]; then protocol="$PWD/$protocol"; else protocol="$repo_dir/$protocol"; fi ;;
esac

if [ ! -f "$protocol" ]; then
    echo "Protocol not found: $protocol" >&2
    exit 1
fi

if ! grep -Eq "^[[:space:]]*(Status[[:space:]]*:[[:space:]]*PRE-REGISTERED|Statut[[:space:]]*:[[:space:]]*PRE-ENREGISTRE)" "$protocol"; then
    echo "Protocol not pre-registered: '$protocol' does not contain 'Status: PRE-REGISTERED' (or the French 'Statut : PRE-ENREGISTRE')." >&2
    echo "A CI cycle requires a protocol approved by the PI." >&2
    exit 1
fi

log_dir="$repo_dir/lab-logs"
mkdir -p "$log_dir"
log_file="$log_dir/lab-$(date +%Y%m%d-%H%M%S).json"

export CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0
cmd=(claude -p "/lab-run '$protocol'" --output-format json --max-turns 150)

if [ "$yolo" -eq 1 ]; then
    cmd+=(--dangerously-skip-permissions)
else
    cmd+=(--permission-mode acceptEdits)
    cmd+=(--allowedTools "@ALLOWED@")
fi

echo "Protocol: $protocol"
echo "Mode    : $([ "$yolo" -eq 1 ] && echo 'YOLO (permissions ignored)' || echo 'acceptEdits + allowlist')"
echo "Log     : $log_file"

set +e
(cd "$repo_dir" && "${cmd[@]}") 2>&1 | tee "$log_file"
status="${PIPESTATUS[0]}"
set -e

echo "claude exit: exit code $status"
exit "$status"
