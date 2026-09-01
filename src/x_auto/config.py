"""Configuration loading: env + YAML, with sensible defaults.

Single source of truth for every setting the app uses. Loaded once at
startup; `get_settings()` returns a cached `Settings` instance.

Resolution order (later wins):
  1. Hard-coded defaults below.
  2. config/settings.yaml.
  3. Environment variables (via python-dotenv on .env).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or None


def load_accounts(config_dir: Path = CONFIG_DIR) -> list[dict[str, str]]:
    """Read, normalize, validate, and de-duplicate monitored X handles."""
    data = _load_yaml(config_dir / "accounts.yaml")
    if data is None:
        return []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("accounts", [])
    else:
        items = []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or "handle" not in item:
            continue
        handle = str(item["handle"]).lstrip("@").strip()
        key = handle.lower()
        if not _HANDLE_RE.fullmatch(handle) or key in seen:
            continue
        seen.add(key)
        out.append({"handle": handle})
    return out


def write_accounts(config_dir: Path, handles: list[str]) -> list[dict[str, str]]:
    """Persist validated handles and return the normalized rows written."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in handles:
        handle = str(raw or "").lstrip("@").strip()
        key = handle.lower()
        if not _HANDLE_RE.fullmatch(handle) or key in seen:
            continue
        seen.add(key)
        rows.append({"handle": handle})
    path = config_dir / "accounts.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Pool of X handles to monitor.\n"
        + yaml.safe_dump(rows, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return rows


@dataclass(frozen=True)
class MinimaxSettings:
    base_url: str
    model_id: str
    temperature: float
    max_tokens: int
    api_key: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class XSettings:
    bearer_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    callback_port: int = 8765
    recent_max_results: int = 5
    exclude: tuple[str, ...] = ("replies", "retweets")
    rate_limit_buffer_seconds: int = 5

    @property
    def configured(self) -> bool:
        return bool(self.bearer_token)


@dataclass(frozen=True)
class UISettings:
    page_title: str = "X-Automation"
    cost_warning_threshold_usd: float = 1.00


@dataclass(frozen=True)
class Settings:
    repo_root: Path
    data_dir: Path
    config_dir: Path
    accounts: tuple[dict[str, str], ...]
    minimax: MinimaxSettings
    x: XSettings
    ui: UISettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)
    raw = _load_yaml(CONFIG_DIR / "settings.yaml")

    mm_raw = raw.get("minimax", {}) or {}
    # Resolution order for base_url: MINIMAX_BASE_URL env var > YAML > default.
    # Without this, the env var was loaded into os.environ but ignored —
    # so users editing .env to point at a different host (e.g. switching
    # between MiniMax regions) saw the live app keep hitting the YAML host.
    minimax = MinimaxSettings(
        base_url=os.environ.get("MINIMAX_BASE_URL", "").strip()
        or str(mm_raw.get("base_url", "https://api.minimax.io/v1")),
        model_id=str(mm_raw.get("model_id", "MiniMax-M2.7")),
        temperature=float(mm_raw.get("temperature", 0.7)),
        # 400 was the historical default but is too low for "thinking"
        # models like MiniMax-M3, which spend ~500 tokens inside a
        # <think>…</think> block before the JSON. 2048 leaves headroom
        # for both the reasoning and the structured output.
        max_tokens=int(mm_raw.get("max_tokens", 2048)),
        api_key=os.environ.get("MINIMAX_API_KEY", "").strip(),
    )

    x_raw = raw.get("x", {}) or {}
    x_settings = XSettings(
        bearer_token=os.environ.get("X_BEARER_TOKEN", "").strip(),
        client_id=os.environ.get("X_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("X_CLIENT_SECRET", "").strip(),
        callback_port=int(os.environ.get("X_AUTH_CALLBACK_PORT", x_raw.get("callback_port", 8765))),
        recent_max_results=int(x_raw.get("recent_max_results", 5)),
        exclude=tuple(x_raw.get("exclude", ["replies", "retweets"])),
        rate_limit_buffer_seconds=int(x_raw.get("rate_limit_buffer_seconds", 5)),
    )

    ui_raw = raw.get("ui", {}) or {}
    ui = UISettings(
        page_title=str(ui_raw.get("page_title", "X-Automation")),
        cost_warning_threshold_usd=float(ui_raw.get("cost_warning_threshold_usd", 1.00)),
    )

    return Settings(
        repo_root=REPO_ROOT,
        data_dir=DATA_DIR,
        config_dir=CONFIG_DIR,
        accounts=tuple(load_accounts()),
        minimax=minimax,
        x=x_settings,
        ui=ui,
    )
