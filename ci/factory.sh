#!/usr/bin/env bash
# Cycle CI headless de l'usine logicielle DuneOS.
# Usage : ci/factory.sh <specs/SPEC-*.md> [--yolo]
set -euo pipefail

usage() {
    echo "Usage: $0 <chemin-spec-approuvee> [--yolo]" >&2
    exit 2
}

SPEC=""
YOLO=0
for arg in "$@"; do
    case "$arg" in
        --yolo) YOLO=1 ;;
        -*) echo "Option inconnue : $arg" >&2; usage ;;
        *) [ -n "$SPEC" ] && usage; SPEC="$arg" ;;
    esac
done
[ -n "$SPEC" ] || usage

if [ ! -f "$SPEC" ]; then
    echo "Erreur : spec introuvable : $SPEC" >&2
    exit 1
fi
if ! grep -q '^Statut : APPROUVEE$' "$SPEC"; then
    echo "Erreur : $SPEC n'est pas approuvée (ligne 'Statut : APPROUVEE' absente)." >&2
    echo "La gate humaine se joue en session interactive : /factory-run \"<besoin>\"" >&2
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

echo "Log : $LOG (exit code claude : $RC)"
exit "$RC"
