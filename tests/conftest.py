"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Set a clean test env BEFORE the config module loads settings.
os.environ.setdefault("X_BEARER_TOKEN", "test-bearer-token-do-not-use")
os.environ.setdefault("X_CLIENT_ID", "test-client-id")
os.environ.setdefault("X_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("MINIMAX_API_KEY", "test-minimax-key")
os.environ.setdefault("X_AUTH_CALLBACK_PORT", "8765")


@pytest.fixture
def configured_settings(tmp_path):
    """A Settings object configured for tests.

    The data_dir is overridden to a tmp_path so the running tests do
    not touch the real data/ folder. The bearer token and MiniMax
    key come from the conftest env above.
    """
    from dataclasses import replace

    from x_auto import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    # Override data_dir to tmp_path so tests don't touch the real data/.
    return replace(s, data_dir=tmp_path / "data")


@pytest.fixture
def tmp_db(tmp_path):
    """A Database bound to a tmp_path file. Closes on teardown."""
    from x_auto.store.repos import Database
    db = Database(tmp_path / "state.db")
    yield db
    db.close()

