"""Tests for virality and engagement velocity scoring."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from x_auto.utils.virality import compute_engagement_velocity, get_hot_opportunity_ids


def test_velocity_zero_when_no_metrics():
    assert compute_engagement_velocity({}, datetime.now(UTC)) == 0.0
    assert compute_engagement_velocity(None, None) == 0.0


def test_velocity_decays_with_time():
    now = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    metrics = {"like_count": 100, "retweet_count": 20, "reply_count": 10}

    # 1 hour ago
    time_1h = now - timedelta(hours=1)
    score_1h = compute_engagement_velocity(metrics, time_1h, reference_time=now)

    # 24 hours ago
    time_24h = now - timedelta(hours=24)
    score_24h = compute_engagement_velocity(metrics, time_24h, reference_time=now)

    assert score_1h > score_24h
    assert score_1h > 100.0


def test_get_hot_opportunity_ids():
    now = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    tweets = [
        {
            "id": "t1",
            "public_metrics": {"like_count": 500, "retweet_count": 100},
            "created_at": now - timedelta(hours=2),
        },
        {
            "id": "t2",
            "public_metrics": {"like_count": 2, "retweet_count": 0},
            "created_at": now - timedelta(hours=5),
        },
        {
            "id": "t3",
            "public_metrics": {"like_count": 200, "retweet_count": 50},
            "created_at": now - timedelta(hours=1),
        },
    ]

    hot_ids = get_hot_opportunity_ids(tweets, top_n=2, min_score=5.0, reference_time=now)
    assert "t1" in hot_ids
    assert "t3" in hot_ids
    assert "t2" not in hot_ids
