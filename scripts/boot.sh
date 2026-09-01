#!/usr/bin/env bash
# Comprehensive one-shot bootstrap for X-Automation on macOS / Linux / WSL.
# Mirror of scripts/boot.ps1 for non-Windows shells.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "==> Repo: $REPO_ROOT"

# venv (the application uses Python 3.11-only features such as datetime.UTC)
python_is_supported() {
    "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
        >/dev/null 2>&1
}

find_supported_python() {
    local candidate
    for candidate in python3.11 python3.12 python3.13 python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

if [[ -x .venv/bin/python ]] && python_is_supported .venv/bin/python; then
    echo "==> .venv present ($(.venv/bin/python --version 2>&1))"
else
    PYTHON_BIN="$(find_supported_python || true)"
    if [[ -z "$PYTHON_BIN" ]]; then
        echo "Python 3.11 or newer is required, but no compatible interpreter was found." >&2
        echo "Install Python 3.11+ and re-run this script." >&2
        exit 1
    fi

    if [[ -d .venv ]]; then
        echo "==> Rebuilding incompatible .venv with $($PYTHON_BIN --version 2>&1)"
        "$PYTHON_BIN" -m venv --clear .venv
    else
        echo "==> Creating .venv with $($PYTHON_BIN --version 2>&1)"
        "$PYTHON_BIN" -m venv .venv
    fi
fi

# deps
echo "==> Installing requirements"
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install -r requirements.txt --quiet

# .env
if [[ ! -f .env ]]; then
    echo "==> Creating .env from .env.example"
    cp .env.example .env
    echo "    Edit .env with your X_BEARER_TOKEN, X_CLIENT_ID,"
    echo "    X_CLIENT_SECRET and MINIMAX_API_KEY, then re-run this script."
    exit 0
fi

# Local user configuration
[[ -f config/accounts.yaml ]] || cp config/accounts.example.yaml config/accounts.yaml
[[ -f data/projects.csv ]] || cp data/projects.example.csv data/projects.csv

# OAuth setup, if needed
if [[ ! -f data/oauth_tokens.json ]]; then
    echo "==> Running scripts/auth_setup.py (opens browser)"
    .venv/bin/python scripts/auth_setup.py
fi

# Launch
echo ""
echo "==> Launching Streamlit on http://localhost:8501"
echo "    Press Ctrl-C to stop."
echo ""
export STREAMLIT_GLOBAL_DEVELOPMENT_MODE=false
exec .venv/bin/streamlit run src/x_auto/app.py \
    --server.headless true \
    --server.port 8501 \
    --client.toolbarMode minimal \
    --browser.gatherUsageStats false
