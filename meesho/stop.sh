#!/usr/bin/env bash
# Run from Git Bash: bash stop.sh
set -eu
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$SCRIPT_DIR"

if [[ -x .venv/Scripts/python.exe ]]; then
    exec .venv/Scripts/python.exe stop_run.py "$@"
elif [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python stop_run.py "$@"
elif command -v python >/dev/null 2>&1; then
    exec python stop_run.py "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 stop_run.py "$@"
elif command -v py >/dev/null 2>&1; then
    exec py -3 stop_run.py "$@"
else
    echo 'Python was not found. Use the Python installation that starts Meesho Ledger.' >&2
    exit 1
fi
