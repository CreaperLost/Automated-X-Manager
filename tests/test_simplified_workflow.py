from __future__ import annotations

from datetime import datetime

import httpx

from x_auto.store.models import Tweet
from x_auto.store.repos import Database
from x_auto.ui.tab_create import _cache_source_image, _tomorrow_rounded
from x_auto.ui.tab_sources import _filter


def test_schedule_default_is_tomorrow_and_rounded():
    now = datetime(2026, 8, 29, 18, 7, 31)
    assert _tomorrow_rounded(now) == datetime(2026, 8, 30, 18, 15)


def test_used_filter_is_derived_from_drafts():
    tweets = [
        Tweet(id="1", account_handle="a", text="alpha", created_at=datetime.now()),
        Tweet(id="2", account_handle="b", text="beta", created_at=datetime.now()),
    ]
    assert [t.id for t in _filter(tweets, "Used", "", {"2"})] == ["2"]
    assert [t.id for t in _filter(tweets, "New", "alpha", {"2"})] == ["1"]


def test_source_image_is_cached_and_registered_before_use(configured_settings, monkeypatch):
    db = Database(configured_settings.data_dir / "state.db")
    source = Tweet(
        id="tweet-1", account_handle="a", text="image",
        created_at=datetime.now(), source_image_url="https://x.test/photo",
    )
    response = httpx.Response(
        200, content=b"not-empty-test-image",
        headers={"content-type": "image/jpeg"},
        request=httpx.Request("GET", source.source_image_url),
    )
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: response)
    path = _cache_source_image(configured_settings, db, source)
    saved = db.get_media_upload_by_path(path)
    assert saved is not None
    assert saved.mime == "image/jpeg"
    assert saved.size == len(response.content)
    db.close()
