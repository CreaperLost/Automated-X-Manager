#!/usr/bin/env bash
# Stop only the detached X-Automation process recorded by start.sh.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO_ROOT="$(pwd)"
PID_FILE="$REPO_ROOT/data/x-automation.pid"

if [[ ! -f "$PID_FILE" ]]; then
    echo "X-Automation is not running (no managed PID found)."
    exit 0
fi

APP_PID="$(tr -d '[:space:]' < "$PID_FILE")"
if [[ ! "$APP_PID" =~ ^[0-9]+$ ]]; then
    echo "Refusing to use an invalid PID file: $PID_FILE" >&2
    exit 1
fi

if ! kill -0 "$APP_PID" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "X-Automation was already stopped; removed the stale PID file."
    exit 0
fi

PROCESS_COMMAND="$(ps -p "$APP_PID" -o command= 2>/dev/null || true)"
if [[ "$PROCESS_COMMAND" != *"streamlit"* || "$PROCESS_COMMAND" != *"src/x_auto/app.py"* ]]; then
    echo "Refusing to stop PID $APP_PID because it is not X-Automation." >&2
    exit 1
fi

kill "$APP_PID"
for _ in {1..20}; do
    if ! kill -0 "$APP_PID" 2>/dev/null; then
        rm -f "$PID_FILE"
        echo "Stopped X-Automation (PID $APP_PID)."
        exit 0
    fi
    sleep 0.25
done

echo "X-Automation did not stop within 5 seconds (PID $APP_PID)." >&2
exit 1
