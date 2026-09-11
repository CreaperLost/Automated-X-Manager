#!/usr/bin/env bash
# Start X-Automation as a detached process for Codex and daily local use.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PID_FILE="$REPO_ROOT/data/x-automation.pid"
LOG_FILE="$REPO_ROOT/data/x-automation.log"
APP_URL="http://localhost:8501"

if [[ ! -x .venv/bin/python || ! -f .env ]]; then
    echo "X-Automation is not set up yet. Run this once first:" >&2
    echo "  bash scripts/boot.sh" >&2
    exit 1
fi

if [[ -f "$PID_FILE" ]]; then
    RUNNING_PID="$(tr -d '[:space:]' < "$PID_FILE")"
    if [[ "$RUNNING_PID" =~ ^[0-9]+$ ]] && kill -0 "$RUNNING_PID" 2>/dev/null; then
        echo "X-Automation is already running (PID $RUNNING_PID)."
        echo "$APP_URL"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:8501 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Port 8501 is already in use; refusing to start another server." >&2
    exit 1
fi

mkdir -p data
nohup bash "$REPO_ROOT/scripts/run.sh" \
    --server.headless true \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --client.toolbarMode minimal \
    --browser.gatherUsageStats false \
    >"$LOG_FILE" 2>&1 &
APP_PID=$!
printf '%s\n' "$APP_PID" > "$PID_FILE"

for _ in {1..30}; do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "X-Automation failed to start. Recent log output:" >&2
        tail -n 30 "$LOG_FILE" >&2 || true
        exit 1
    fi
    if curl -fsS "$APP_URL/_stcore/health" >/dev/null 2>&1; then
        echo "Started X-Automation (PID $APP_PID)."
        echo "$APP_URL"
        exit 0
    fi
    sleep 0.5
done

echo "X-Automation is starting (PID $APP_PID)."
echo "Log: $LOG_FILE"
echo "$APP_URL"
