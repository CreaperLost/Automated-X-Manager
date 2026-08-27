#!/usr/bin/env bash
# Convenience: launch the Streamlit app from the repo root.
# Works on macOS / Linux. On Windows, run:
#     streamlit run src/x_auto/app.py
set -euo pipefail
cd "$(dirname "$0")/.."
exec streamlit run src/x_auto/app.py "$@"
