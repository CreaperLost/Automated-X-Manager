"""Tests for performance feedback store."""
from __future__ import annotations

from pathlib import Path

from x_auto.store.performance import is_high_performer, load_winners, save_winner


def test_save_and_load_winners(tmp_path: Path) -> None:
    # Initially empty
    assert load_winners(tmp_path) == []

    # Save winner 1
    save_winner(
        tmp_path,
        draft_id="101",
        body="High performing take on AI agents.",
        metrics={"like_count": 10, "retweet_count": 2, "reply_count": 1},
        cta_text="Check the course: https://udemy.com/ai",
    )

    # Save winner 2 (even higher engagement)
    save_winner(
        tmp_path,
        draft_id="102",
        body="Super viral take on LangGraph workflows.",
        metrics={"like_count": 50, "retweet_count": 10, "reply_count": 5},
        cta_text="Become an agent engineer: https://udemy.com/agents",
    )

    winners = load_winners(tmp_path, limit=5)
    assert len(winners) == 2
    # Winner 102 should be first because score is higher
    assert winners[0]["draft_id"] == "102"
    assert winners[1]["draft_id"] == "101"


def test_is_high_performer() -> None:
    assert not is_high_performer({"like_count": 1, "retweet_count": 0, "reply_count": 0})
    assert is_high_performer({"like_count": 3, "retweet_count": 0, "reply_count": 0})
    assert is_high_performer({"like_count": 0, "retweet_count": 1, "reply_count": 0})
    assert is_high_performer({"like_count": 0, "retweet_count": 0, "reply_count": 2})
