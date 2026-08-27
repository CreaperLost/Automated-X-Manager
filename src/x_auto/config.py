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
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = REPO_ROOT / "data"
CONFIG_DIR = REPO_ROOT / "config"


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or None


def _load_accounts() -> list[dict[str, str]]:
    data = _load_yaml(CONFIG_DIR / "accounts.yaml")
    items = data if isinstance(data, list) else data.get("accounts", [])
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or "handle" not in item:
            continue
        out.append({"handle": str(item["handle"]).lstrip("@").strip()})
    return out


def _load_projects() -> tuple[dict[str, Any], ...]:
    """Return projects from config/projects.yaml if it exists.

    Note: the canonical source of project:link data is now
    data/projects.csv, loaded at app startup by `sync_projects()` and
    editable from the Settings tab. The legacy YAML path is kept as a
    one-time bootstrap fallback so a fresh clone (no CSV yet) can still
    surface the two example projects in the Settings editor.
    """
    path = CONFIG_DIR / "projects.yaml"
    if not path.exists():
        return ()
    try:
        data = _load_yaml(path)
    except Exception:
        return ()
    items = data if isinstance(data, list) else (data.get("projects") if isinstance(data, dict) else None)
    if not isinstance(items, list):
        return ()
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or "name" not in item or "url" not in item:
            continue
        out.append(
            {
                "name": str(item["name"]).strip(),
                "url": str(item["url"]).strip(),
                "description": str(item.get("description", "")).strip(),
                "tags": [str(t).strip() for t in (item.get("tags") or []) if str(t).strip()],
            }
        )
    return tuple(out)


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
    recent_max_results: int = 20
    exclude: tuple[str, ...] = ("replies", "retweets")
    rate_limit_buffer_seconds: int = 5

    @property
    def configured(self) -> bool:
        return bool(self.bearer_token)


@dataclass(frozen=True)
class ScheduleSettings:
    max_lookahead_days: int = 30
    min_lead_minutes: int = 5


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
    projects: tuple[dict[str, Any], ...]
    minimax: MinimaxSettings
    x: XSettings
    schedule: ScheduleSettings
    ui: UISettings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env", override=False)
    raw = _load_yaml(CONFIG_DIR / "settings.yaml")

    mm_raw = raw.get("minimax", {}) or {}
    # Resolution order for base_url: MINIMAX_BASE_URL env var > YAML > default.
    # Without this, the env var was loaded into os.environ but ignored —
    # so users editing .env to point at a different host (e.g. switching
    # from api.minimaxi.com to api.minimax.io when the key rotates to a
    # new region) saw the live app keep hitting the YAML host.
    minimax = MinimaxSettings(
        base_url=os.environ.get("MINIMAX_BASE_URL", "").strip()
        or str(mm_raw.get("base_url", "https://api.minimaxi.com/v1")),
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
        recent_max_results=int(x_raw.get("recent_max_results", 20)),
        exclude=tuple(x_raw.get("exclude", ["replies", "retweets"])),
        rate_limit_buffer_seconds=int(x_raw.get("rate_limit_buffer_seconds", 5)),
    )

    sched_raw = raw.get("schedule", {}) or {}
    schedule = ScheduleSettings(
        max_lookahead_days=int(sched_raw.get("max_lookahead_days", 30)),
        min_lead_minutes=int(sched_raw.get("min_lead_minutes", 5)),
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
        accounts=tuple(_load_accounts()),
        projects=tuple(_load_projects()),
        minimax=minimax,
        x=x_settings,
        schedule=schedule,
        ui=ui,
    )
