"""Tests for multi-niche support (Crypto vs AI)."""
from __future__ import annotations

from pathlib import Path

import pytest

from x_auto.ai.projects import csv_path, list_projects, load_csv, sync_projects, write_csv
from x_auto.config import get_settings, load_accounts, write_accounts
from x_auto.store.repos import Database


@pytest.fixture(autouse=True)
def _clear_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_crypto_settings_resolution():
    s = get_settings("crypto")
    assert s.niche == "crypto"
    assert s.config_dir.name == "crypto"
    assert s.data_dir.name == "crypto"
    assert "Crypto" in s.ui.page_title or "X-Automation" in s.ui.page_title
    handles = [a["handle"].lower() for a in s.accounts]
    assert "skyisthetaker" in handles or "exsa_mui" in handles


def test_ai_settings_resolution():
    s = get_settings("ai")
    assert s.niche == "ai"
    assert s.config_dir.name == "ai"
    assert s.data_dir.name == "ai"
    assert "AI" in s.ui.page_title
    handles = [a["handle"].lower() for a in s.accounts]
    assert "emollick" in handles or "bindureddy" in handles


def test_projects_csv_separation():
    crypto_s = get_settings("crypto")
    ai_s = get_settings("ai")

    crypto_csv = csv_path(crypto_s)
    ai_csv = csv_path(ai_s)

    assert crypto_csv != ai_csv
    assert "crypto" in str(crypto_csv)
    assert "ai" in str(ai_csv)

    crypto_projects = load_csv(crypto_csv)
    ai_projects = load_csv(ai_csv)

    assert len(crypto_projects) > 0
    assert len(ai_projects) > 0

    crypto_names = {p["name"] for p in crypto_projects}
    ai_names = {p["name"] for p in ai_projects}

    # Verify they contain their respective activations
    assert "Hyperliquid" in crypto_names or "Propr" in crypto_names
    assert "Become AI Agent Engineer in 2026" in ai_names or "Master AI in 2026" in ai_names

    # The sets of activations should be completely distinct
    assert crypto_names != ai_names


def test_db_sync_per_niche(tmp_path: Path):
    crypto_s = get_settings("crypto")
    ai_s = get_settings("ai")

    db_crypto = Database(tmp_path / "crypto_state.db")
    db_ai = Database(tmp_path / "ai_state.db")

    n_crypto = sync_projects(crypto_s, db_crypto)
    n_ai = sync_projects(ai_s, db_ai)

    assert n_crypto > 0
    assert n_ai > 0

    loaded_crypto = list_projects(db_crypto)
    loaded_ai = list_projects(db_ai)

    assert len(loaded_crypto) == n_crypto
    assert len(loaded_ai) == n_ai

    crypto_names = {p["name"] for p in loaded_crypto}
    ai_names = {p["name"] for p in loaded_ai}

    assert "Hyperliquid" in crypto_names or "Propr" in crypto_names
    assert "Become AI Agent Engineer in 2026" in ai_names or "Master AI in 2026" in ai_names

    db_crypto.close()
    db_ai.close()


def test_creators_write_isolation(tmp_path: Path):
    crypto_cfg = tmp_path / "config" / "crypto"
    ai_cfg = tmp_path / "config" / "ai"
    crypto_cfg.mkdir(parents=True)
    ai_cfg.mkdir(parents=True)

    write_accounts(crypto_cfg, ["CryptoWhale", "AirdropHunter"])
    write_accounts(ai_cfg, ["AIEngineer", "MLResearcher"])

    crypto_accounts = load_accounts(crypto_cfg)
    ai_accounts = load_accounts(ai_cfg)

    assert [a["handle"] for a in crypto_accounts] == ["CryptoWhale", "AirdropHunter"]
    assert [a["handle"] for a in ai_accounts] == ["AIEngineer", "MLResearcher"]


def test_projects_write_isolation(tmp_path: Path):
    crypto_csv = tmp_path / "data" / "crypto" / "projects.csv"
    ai_csv = tmp_path / "data" / "ai" / "projects.csv"

    write_csv(crypto_csv, [{"name": "DeFiProtocol", "url": "https://defi.test"}])
    write_csv(ai_csv, [{"name": "LLMModel", "url": "https://llm.test"}])

    assert load_csv(crypto_csv) == [
        {"name": "DeFiProtocol", "url": "https://defi.test", "description": "", "tags": []}
    ]
    assert load_csv(ai_csv) == [
        {"name": "LLMModel", "url": "https://llm.test", "description": "", "tags": []}
    ]
