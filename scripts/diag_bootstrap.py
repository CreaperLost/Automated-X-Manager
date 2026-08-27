"""Boot the Streamlit app's _bootstrap() the same way the first rerun would.

This catches any runtime error in the bootstrap path that would
otherwise show up only inside Streamlit. Uses the real .env file so
it can talk to the real X API for a real "is everything wired"
signal.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Make sure .env is loaded (python-dotenv needs the working dir to be
# the repo root).
os.chdir(ROOT)

print("=== bootstrap (real config, real .env) ===")
try:
    # Import the app module fresh.
    from x_auto import app
    settings, db, x_client, ai, scheduler, token_manager = app._bootstrap.__wrapped__()
    print(f"  settings.repo_root  : {settings.repo_root}")
    print(f"  settings.data_dir   : {settings.data_dir}")
    print(f"  x.configured        : {settings.x.configured}")
    print(f"  miniMax.configured  : {settings.minimax.configured}")
    print(f"  auth tokens on disk : {bool(token_manager._store.load())}")
    print(f"  accounts in DB      : {len(db.list_accounts())}")
    print(f"  projects in DB      : {len(db.list_projects())}")
    print(f"  scheduler running   : {scheduler.running}")
    print()
    print("OK: bootstrap completed cleanly.")
except Exception:
    traceback.print_exc()
    print()
    print("FAIL: bootstrap raised.")
    sys.exit(1)
