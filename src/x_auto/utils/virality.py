"""Engagement velocity and opportunity scoring for fetched tweets."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_datetime(dt_val: datetime | str | None) -> datetime:
    """Parse datetime or ISO string to datetime with UTC timezone awareness."""
    if dt_val is None:
        return datetime.now(UTC)
    if isinstance(dt_val, datetime):
        return dt_val if dt_val.tzinfo else dt_val.replace(tzinfo=UTC)
    try:
        clean = dt_val.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(clean)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def compute_engagement_velocity(
    metrics: dict[str, int] | None,
    created_at: datetime | str | None,
    reference_time: datetime | None = None,
) -> float:
    """Calculate engagement velocity score (engagement per decayed hour).

    Formula:
      raw = (likes * 1.0) + (retweets * 2.0) + (quotes * 2.5) + (replies * 1.5)
      velocity = raw / (hours_elapsed ^ 0.8)
    """
    m = metrics or {}
    likes = float(m.get("like_count", 0))
    retweets = float(m.get("retweet_count", 0))
    quotes = float(m.get("quote_count", 0))
    replies = float(m.get("reply_count", 0))

    raw_engagement = (likes * 1.0) + (retweets * 2.0) + (quotes * 2.5) + (replies * 1.5)
    if raw_engagement <= 0:
        return 0.0

    ref = reference_time or datetime.now(UTC)
    if not ref.tzinfo:
        ref = ref.replace(tzinfo=UTC)

    post_time = parse_datetime(created_at)
    diff_seconds = max(60.0, (ref - post_time).total_seconds())
    hours = max(0.25, diff_seconds / 3600.0)

    # Time-decayed velocity
    velocity = raw_engagement / (hours**0.8)
    return round(velocity, 2)


def get_hot_opportunity_ids(
    tweets: list[Any],
    top_n: int = 5,
    min_score: float = 2.0,
    reference_time: datetime | None = None,
) -> set[str]:
    """Return a set of tweet IDs that represent the highest-velocity opportunities."""
    scored: list[tuple[str, float]] = []
    for t in tweets:
        tid = getattr(t, "id", None) or (t.get("id") if isinstance(t, dict) else None)
        if not tid:
            continue
        metrics = getattr(t, "public_metrics", None) or (
            t.get("public_metrics") if isinstance(t, dict) else {}
        )
        created_at = getattr(t, "created_at", None) or (
            t.get("created_at") if isinstance(t, dict) else None
        )
        score = compute_engagement_velocity(metrics, created_at, reference_time)
        if score >= min_score:
            scored.append((str(tid), score))

    scored.sort(key=lambda item: item[1], reverse=True)
    return {tid for tid, _ in scored[:top_n]}
