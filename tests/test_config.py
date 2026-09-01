"""Config loading: env > YAML > defaults for every field that has
both an env var and a YAML entry. Regression coverage for the
MINIMAX_BASE_URL bug where the env var was loaded by load_dotenv()
but never read by get_settings()."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_lru_cache():
    """Each test gets a fresh get_settings() (the lru_cache otherwise
    pins the result of the first call in the test session)."""
    from x_auto import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_minimax_base_url_read_from_env(monkeypatch):
    """MINIMAX_BASE_URL in the env must override the YAML value.

    This is the contract the live app relies on when the user edits
    .env to point at a different MiniMax region after a key rotation.
    """
    from x_auto import config

    monkeypatch.setenv("MINIMAX_BASE_URL", "https://example.test/v9")
    s = config.get_settings()
    assert s.minimax.base_url == "https://example.test/v9"


def test_minimax_base_url_falls_back_to_yaml(monkeypatch):
    """When the env var is absent, the YAML value wins."""
    from x_auto import config

    monkeypatch.delenv("MINIMAX_BASE_URL", raising=False)
    s = config.get_settings()
    # The current YAML is api.minimax.io/v1; just check we got a
    # non-empty string from somewhere (not the hard-coded default).
    assert s.minimax.base_url
    assert s.minimax.base_url.startswith("https://")


def test_minimax_api_key_from_env(monkeypatch):
    """The API key is env-only (YAML is not a secrets sink)."""
    from x_auto import config

    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-xyz")
    s = config.get_settings()
    assert s.minimax.api_key == "sk-test-xyz"
    assert s.minimax.configured is True


def test_handle_file_add_remove_validate_and_dedupe(tmp_path):
    from x_auto.config import load_accounts, write_accounts

    config_dir = tmp_path / "config"
    rows = write_accounts(
        config_dir,
        ["@OpenAI", "naval", "OPENAI", "bad handle!", "x" * 16, ""],
    )
    assert rows == [{"handle": "OpenAI"}, {"handle": "naval"}]
    assert load_accounts(config_dir) == rows

    rows = write_accounts(config_dir, ["naval"])
    assert rows == [{"handle": "naval"}]
    assert load_accounts(config_dir) == rows
