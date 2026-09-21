"""Performance tracking and winning exemplar store for self-improving feedback loop.

Logs high-performing published tweets to `data/<niche>/winners.json`
so they can be injected as few-shot exemplars into future draft generations.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


def winners_path(data_dir: Path) -> Path:
    """Path to the niche's winners.json store."""
    return data_dir / "winners.json"


def load_winners(data_dir: Path, limit: int = 3) -> list[dict[str, Any]]:
    """Load winning posts sorted by performance score descending."""
    path = winners_path(data_dir)
    if not path.is_file():
        return []

    try:
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []
        data = json.loads(content)
        if not isinstance(data, list):
            return []
    except (json.JSONDecodeError, OSError):
        return []

    def _score(item: dict[str, Any]) -> float:
        m = item.get("metrics") or {}
        likes = float(m.get("like_count", 0))
        retweets = float(m.get("retweet_count", 0))
        replies = float(m.get("reply_count", 0))
        quotes = float(m.get("quote_count", 0))
        return likes + (retweets * 2.0) + (replies * 1.5) + (quotes * 2.0)

    sorted_winners = sorted(data, key=_score, reverse=True)
    return sorted_winners[:limit]


def save_winner(
    data_dir: Path,
    draft_id: int | str,
    body: str,
    metrics: dict[str, Any],
    cta_text: str = "",
) -> dict[str, Any]:
    """Save or update a winning post in winners.json atomically."""
    path = winners_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: list[dict[str, Any]] = []
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8").strip()
            if content:
                loaded = json.loads(content)
                if isinstance(loaded, list):
                    existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []

    clean_body = body.strip()
    entry: dict[str, Any] = {
        "draft_id": str(draft_id),
        "body": clean_body,
        "cta_text": cta_text.strip(),
        "metrics": {
            "like_count": int(metrics.get("like_count", 0)),
            "retweet_count": int(metrics.get("retweet_count", 0)),
            "reply_count": int(metrics.get("reply_count", 0)),
            "quote_count": int(metrics.get("quote_count", 0)),
            "impression_count": int(metrics.get("impression_count", 0)),
        },
        "saved_at": datetime.now().isoformat(),
    }

    # Update existing entry if present, else append
    updated = False
    for i, item in enumerate(existing):
        if str(item.get("draft_id")) == str(draft_id):
            existing[i] = entry
            updated = True
            break
    if not updated:
        existing.append(entry)

    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return entry


def is_high_performer(metrics: dict[str, Any], like_threshold: int = 3) -> bool:
    """Return True if public metrics exceed winner threshold."""
    likes = int(metrics.get("like_count", 0))
    retweets = int(metrics.get("retweet_count", 0))
    replies = int(metrics.get("reply_count", 0))
    return likes >= like_threshold or retweets >= 1 or replies >= 2
