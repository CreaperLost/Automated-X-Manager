#!/usr/bin/env bash
# Comprehensive one-shot bootstrap for X-Automation on macOS / Linux / WSL.
# Mirror of scripts/boot.ps1 for non-Windows shells.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
echo "==> Repo: $REPO_ROOT"

# venv
if [[ ! -x .venv/bin/python ]]; then
    echo "==> Creating .venv"
    python3 -m venv .venv
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

# Phase 0 verification
echo "==> Running scripts/verify_setup.py"
.venv/bin/python scripts/verify_setup.py

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
exec .venv/bin/streamlit run src/x_auto/app.py \
    --server.headless true \
    --server.port 8501 \
    --client.toolbarMode minimal \
    --browser.gatherUsageStats false
