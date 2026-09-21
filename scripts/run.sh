#!/usr/bin/env bash
# Launch the Streamlit app in the foreground from the repo root.
# Use start.sh for a detached process that can be stopped with stop.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_PYTHON=""
for candidate in .venv/bin/python .venv/Scripts/python.exe; do
    if [[ -x "$candidate" ]]; then
        VENV_PYTHON="$candidate"
        break
    fi
done

if [[ -z "$VENV_PYTHON" ]]; then
    echo "X-Automation is not set up yet. Run: bash scripts/boot.sh" >&2
    exit 1
fi
if ! "$VENV_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
    >/dev/null 2>&1; then
    echo "X-Automation requires Python 3.11+. Rebuild it with: bash scripts/boot.sh" >&2
    exit 1
fi

export STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false
exec "$VENV_PYTHON" -m streamlit run src/x_auto/app.py "$@"
