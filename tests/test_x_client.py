"""X API client tests with respx recorded-fixture-style mocks.

We do NOT make real network calls. We mock httpx using respx so the
client logic (URL building, auth, retry, error mapping, cost
metering) can be tested in isolation.

For tests that need user-context auth, we seed a TokenBundle into
the store so the XClient can find it.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from x_auto.config import Settings
from x_auto.x.auth import TokenBundle, TokenManager, TokenStore
from x_auto.x.client import (
    API_BASE,
    AuthExpiredError,
    RateLimitedError,
    XApiError,
    XClient,
)


def _seed_tokens(settings: Settings, tmp_path: Path) -> TokenStore:
    """Write a fresh token bundle to a tmp store and return it."""
    store = TokenStore(tmp_path / "oauth.json")
    bundle = TokenBundle(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        expires_at=datetime.now(UTC) + timedelta(hours=2),
        scope="tweet.read tweet.write users.read media.write offline.access",
        bearer_token=settings.x.bearer_token,
    )
    store.save(bundle)
    return store


@pytest.fixture
def x_client(configured_settings, tmp_path):
    """An XClient with seeded tokens and an in-process httpx mock ready.

    The bearer token in settings is already populated by conftest.
    The user-context access token is written to a tmp_path TokenStore
    so the manager can find it.
    """
    store = _seed_tokens(configured_settings, tmp_path)
    token_manager = TokenManager(configured_settings, store=store)
    c = XClient(configured_settings, token_manager=token_manager)
    yield c
    try:
        asyncio.run(c.aclose())
    except Exception:
        pass


# ---- bearer-auth reads --------------------------------------------------------

def test_get_user_by_username_records_cost(x_client: XClient):
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.get("/users/by/username/naval").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"id": "123", "username": "naval", "name": "Naval"}},
            )
        )
        user = asyncio.run(x_client.get_user_by_username("naval"))
    assert user.id == "123"
    assert user.username == "naval"
    s = x_client.meter.summary()
    assert s["profiles_read"] == 1
    assert s["reads_cost_usd"] == 0.010


def test_get_user_tweets_dedupes_cost_per_tweet(x_client: XClient):
    payload = {
        "data": [
            {"id": "1", "text": "a", "author_id": "123",
             "created_at": "2026-01-01T00:00:00.000Z",
             "public_metrics": {"like_count": 1}},
            {"id": "2", "text": "b", "author_id": "123",
             "created_at": "2026-01-02T00:00:00.000Z",
             "public_metrics": {}},
        ]
    }
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.get("/users/123/tweets").mock(
            return_value=httpx.Response(200, json=payload)
        )
        tweets = asyncio.run(x_client.get_user_tweets("123", max_results=20))
    assert len(tweets) == 2
    assert tweets[0].id == "1"
    s = x_client.meter.summary()
    assert s["posts_read"] == 2
    assert s["reads_cost_usd"] == 2 * 0.005


def test_get_user_tweets_extracts_first_photo_only(x_client: XClient):
    payload = {
        "data": [{
            "id": "10", "text": "with media", "author_id": "123",
            "created_at": "2026-01-01T00:00:00.000Z", "public_metrics": {},
            "attachments": {"media_keys": ["video-1", "photo-1", "photo-2"]},
        }],
        "includes": {"media": [
            {"media_key": "video-1", "type": "video", "preview_image_url": "https://x/video.jpg"},
            {"media_key": "photo-1", "type": "photo", "url": "https://x/first.jpg"},
            {"media_key": "photo-2", "type": "photo", "url": "https://x/second.jpg"},
        ]},
    }
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.get("/users/123/tweets").mock(return_value=httpx.Response(200, json=payload))
        tweets = asyncio.run(x_client.get_user_tweets("123", max_results=5))
    assert tweets[0].source_image_url == "https://x/first.jpg"


def test_rate_limit_raises_after_one_retry(x_client: XClient):
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.get("/users/by/username/naval").mock(
            return_value=httpx.Response(429, headers={"retry-after": "1"},
                                       text="rate limited")
        )
        with pytest.raises(RateLimitedError):
            asyncio.run(x_client.get_user_by_username("naval"))


def test_404_raises_xapierror(x_client: XClient):
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.get("/users/by/username/nonexistent").mock(
            return_value=httpx.Response(404, text="not found")
        )
        with pytest.raises(XApiError) as exc:
            asyncio.run(x_client.get_user_by_username("nonexistent"))
    assert exc.value.status == 404


# ---- user-auth writes ---------------------------------------------------------

def test_create_post_records_write_cost(x_client: XClient):
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        mock.post("/tweets").mock(
            return_value=httpx.Response(200, json={"data": {"id": "777"}})
        )
        tid = asyncio.run(x_client.create_post("hello"))
    assert tid == "777"
    s = x_client.meter.summary()
    assert s["writes_cost_usd"] == 0.015


def test_create_post_with_reply(x_client: XClient):
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        route = mock.post("/tweets").mock(
            return_value=httpx.Response(200, json={"data": {"id": "888"}})
        )
        tid = asyncio.run(x_client.create_post("https://x.com", reply_to="777"))
    assert tid == "888"
    # The request body should contain in_reply_to_tweet_id.
    assert len(route.calls) == 1
    request = route.calls[0].request
    body = request.content.decode() if request.content else ""
    assert "777" in body
    assert "in_reply_to_tweet_id" in body


def test_401_raises_auth_expired_after_retry(x_client: XClient, tmp_path):
    """A 401 should be retried once with a (failing) refresh, then raise."""
    with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
        # First call: 401. Retry after refresh: 401 again.
        mock.post("/tweets").mock(
            return_value=httpx.Response(401, text="unauthorized")
        )
        # The refresh attempt itself hits the token endpoint and fails.
        mock.post("/oauth2/token").mock(
            return_value=httpx.Response(400, text="invalid_grant")
        )
        with pytest.raises(AuthExpiredError):
            asyncio.run(x_client.create_post("hi"))
