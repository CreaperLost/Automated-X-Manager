"""Repository CRUD on an in-memory SQLite (via tmp_path)."""
from __future__ import annotations

import pytest

from x_auto.store.repos import Database


@pytest.fixture
def db(tmp_path) -> Database:
    d = Database(tmp_path / "state.db")
    yield d
    d.close()


def test_upsert_account(db: Database):
    db.upsert_account("naval", "1", "Naval")
    rows = db.list_accounts()
    assert len(rows) == 1
    assert rows[0].handle == "naval"
    assert rows[0].user_id == "1"
    # Upsert overwrites user_id.
    db.upsert_account("naval", "1-new", "Naval Ravikant")
    assert db.list_accounts()[0].user_id == "1-new"


def test_upsert_tweets_dedup(db: Database):
    db.upsert_account("naval", "1")
    payload = [
        {"id": "100", "text": "hello", "created_at": "2026-01-01T00:00:00Z",
         "public_metrics": {"like_count": 1, "retweet_count": 0}},
        {"id": "101", "text": "world", "created_at": "2026-01-02T00:00:00Z",
         "public_metrics": {}},
    ]
    n = db.upsert_tweets("naval", payload)
    assert n == 2
    # Re-upserting the same ids returns 0 new.
    n2 = db.upsert_tweets("naval", payload)
    assert n2 == 0
    assert len(db.list_tweets()) == 2


def test_quote_metadata_roundtrip(db: Database):
    db.upsert_account("naval", "1")
    db.upsert_tweets("naval", [{
        "id": "quoted-source",
        "text": "My commentary",
        "created_at": "2026-01-01T00:00:00Z",
        "public_metrics": {},
        "quote_tweet_id": "quoted-original",
        "quote_tweet_text": "The original post",
        "quote_tweet_author_id": "2",
        "source_image_url": "https://pbs.twimg.com/media/example.jpg",
    }])
    tweet = db.get_tweet("quoted-source")
    assert tweet is not None
    assert tweet.quote_tweet_id == "quoted-original"
    assert tweet.quote_tweet_text == "The original post"
    assert tweet.source_image_url == "https://pbs.twimg.com/media/example.jpg"

    from x_auto.store.models import Draft
    draft_id = db.create_draft(Draft(
        body="my take", quote_tweet_id="quoted-original", status="draft"
    ))
    draft = db.get_draft(draft_id)
    assert draft is not None
    assert draft.quote_tweet_id == "quoted-original"
    assert draft.writing_mode == "rephrase"

    original_id = db.create_draft(Draft(
        body="new angle", writing_mode="original_take", status="draft"
    ))
    assert db.get_draft(original_id).writing_mode == "original_take"


def test_set_tweet_statuses(db: Database):
    db.upsert_account("naval", "1")
    db.upsert_tweets("naval", [
        {"id": "1", "text": "a", "created_at": "2026-01-01T00:00:00Z", "public_metrics": {}},
        {"id": "2", "text": "b", "created_at": "2026-01-01T00:00:00Z", "public_metrics": {}},
    ])
    n = db.set_tweet_statuses(["1", "2"], "selected")
    assert n == 2
    assert len(db.list_tweets(status="selected")) == 2
    assert len(db.list_tweets(status="new")) == 0


def test_draft_lifecycle(db: Database):
    from datetime import datetime

    from x_auto.store.models import Draft
    d = Draft(source_tweet_id=None, body="hello", link_url="https://x.com",
              image_paths=[], tone="neutral", status="draft")
    draft_id = db.create_draft(d)
    assert draft_id > 0
    loaded = db.get_draft(draft_id)
    assert loaded is not None
    assert loaded.body == "hello"
    assert loaded.status == "draft"
    # Promote to final.
    loaded.status = "final"
    loaded.finalized_at = datetime.now()
    db.update_draft(loaded)
    finals = db.list_drafts(status="final")
    assert any(fd.id == draft_id for fd in finals)


def test_post_log_roundtrip(db: Database):
    from x_auto.store.models import Draft
    db.upsert_account("naval", "1")
    db.upsert_tweets("naval", [
        {"id": "1", "text": "a", "created_at": "2026-01-01T00:00:00Z", "public_metrics": {}},
    ])
    # The post_log has a FK to drafts; create a real draft first.
    draft_id = db.create_draft(Draft(body="hello", status="final"))
    log_id = db.log_post(draft_id, "post_now", 0.030, "success", "x_tweet_id=10")
    assert log_id > 0
    rows = db.recent_log()
    assert rows[0]["action"] == "post_now"
    assert rows[0]["cost_usd"] == 0.030
    assert db.total_session_cost() == 0.030
