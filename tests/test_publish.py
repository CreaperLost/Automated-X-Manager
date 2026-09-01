"""Tests for the shared ``x_auto.x.publish`` module.

These tests pin the shared publish contract: one main + one reply,
draft status flips to "posted", and a log row is written.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from x_auto.config import Settings
from x_auto.store.models import Draft
from x_auto.store.repos import Database
from x_auto.x.auth import TokenBundle, TokenManager, TokenStore
from x_auto.x.client import API_BASE, XClient
from x_auto.x.publish import PublishValidationError, publish_draft


def _seed_tokens(settings: Settings, tmp_path: Path) -> TokenStore:
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
    store = _seed_tokens(configured_settings, tmp_path)
    token_manager = TokenManager(configured_settings, store=store)
    c = XClient(configured_settings, token_manager=token_manager)
    yield c
    try:
        asyncio.run(c.aclose())
    except Exception:
        pass


def _make_draft(db: Database, **overrides) -> Draft:
    defaults = dict(
        source_tweet_id=None,
        body="main body",
        link_url="https://example.com",
        image_paths=[],
        tone="",
        status="draft",
    )
    defaults.update(overrides)
    d = Draft(**defaults)
    d.id = db.create_draft(d)
    return d


class TestPublishDraft:
    def test_video_must_be_the_only_attachment(
        self, configured_settings, x_client, tmp_db: Database, tmp_path: Path
    ):
        video = tmp_path / "clip.mp4"
        image = tmp_path / "poster.png"
        video.write_bytes(b"video")
        image.write_bytes(b"image")
        draft = _make_draft(
            tmp_db,
            image_paths=[str(video), str(image)],
            link_url=None,
        )

        with pytest.raises(PublishValidationError, match="only media attachment"):
            asyncio.run(publish_draft(configured_settings, tmp_db, x_client, draft))

    def test_legacy_third_party_quote_is_removed_before_write(
        self, configured_settings, x_client, tmp_db: Database
    ):
        draft = _make_draft(
            tmp_db, body="inspired take", link_url=None,
            quote_tweet_id="third-party-quote",
        )
        with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
            route = mock.post("/tweets").mock(
                return_value=httpx.Response(200, json={"data": {"id": "safe-1"}})
            )
            asyncio.run(publish_draft(configured_settings, tmp_db, x_client, draft))
        assert "quote_tweet_id" not in route.calls[0].request.content.decode()
        assert tmp_db.get_draft(draft.id).quote_tweet_id is None

    def test_invalid_body_is_blocked_before_any_x_write(
        self, configured_settings, x_client, tmp_db: Database
    ):
        draft = _make_draft(tmp_db, body="visit https://inline.example", link_url=None)
        with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
            route = mock.post("/tweets")
            with pytest.raises(PublishValidationError):
                asyncio.run(
                    publish_draft(configured_settings, tmp_db, x_client, draft)
                )
        assert route.call_count == 0

    def test_post_now_flips_status_and_writes_log(
        self, configured_settings, x_client, tmp_db: Database
    ):
        """Posting a draft sets status='posted', fills x_tweet_id /
        x_reply_id / cost_usd, and writes a post_log row."""
        draft = _make_draft(
            tmp_db,
            body="hello world",
            link_url="https://example.com",
        )
        with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
            route = mock.post("/tweets").mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"id": "111"}}),
                    httpx.Response(200, json={"data": {"id": "222"}}),
                ]
            )
            result = asyncio.run(
                publish_draft(configured_settings, tmp_db, x_client, draft)
            )

        # The shared module returns a typed result.
        assert result.x_tweet_id == "111"
        assert result.x_reply_id == "222"
        # Total cost: $0.015 (main) + $0.015 (reply) = $0.030
        assert result.cost_usd == pytest.approx(0.030, abs=1e-9)

        # The DB row reflects the published state.
        row = tmp_db.get_draft(draft.id)
        assert row is not None
        assert row.status == "posted"
        assert row.x_tweet_id == "111"
        assert row.x_reply_id == "222"
        assert row.cost_usd == pytest.approx(0.030, abs=1e-9)
        assert row.posted_at is not None

        # A log row was written.
        log = tmp_db.recent_log(limit=5)
        post_now = [e for e in log if e["action"] == "post_now"]
        assert len(post_now) == 1
        assert post_now[0]["result"] == "success"
        assert post_now[0]["draft_id"] == draft.id

        # Two POSTs were made: main + reply.
        assert route.call_count == 2

    def test_post_now_without_link_url_skips_reply(
        self, configured_settings, x_client, tmp_db: Database
    ):
        """If there's no project link, no reply is created and the
        cost drops to $0.015 (a single plain post)."""
        draft = _make_draft(tmp_db, body="plain", link_url=None)
        with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
            route = mock.post("/tweets").mock(
                return_value=httpx.Response(200, json={"data": {"id": "333"}})
            )
            result = asyncio.run(
                publish_draft(configured_settings, tmp_db, x_client, draft)
            )
        assert result.x_tweet_id == "333"
        assert result.x_reply_id is None
        assert result.cost_usd == pytest.approx(0.015, abs=1e-9)
        # Only one POST.
        assert route.call_count == 1


class TestPublishDraftSync:
    """Regression tests for the ``publish_draft_sync`` wrapper.

    The earlier version reused the long-lived ``XClient._http``
    (httpx ``AsyncClient``) across ``asyncio.run`` calls. Each call
    closes the loop it created, leaving the persistent httpx
    connection pool bound to a dead loop — the next call then raises
    ``RuntimeError: Event loop is closed``. The fix builds a one-shot
    ``XClient`` per call so the connection pool lives for the
    duration of one ``asyncio.run``.
    """

    def test_publish_draft_sync_can_be_called_repeatedly(
        self, configured_settings, x_client, tmp_db: Database
    ):
        """Calling ``publish_draft_sync`` three times in a row must
        not raise ``RuntimeError: Event loop is closed`` and each
        draft must end up posted. (Regression for the
        persistent-httpx-client bug introduced in v0.1.)"""
        from x_auto.x.publish import publish_draft_sync

        ids: list[int] = []
        for i in range(3):
            draft = _make_draft(tmp_db, body=f"call-{i}", link_url=None)
            ids.append(draft.id)
            with respx.mock(
                base_url=API_BASE, assert_all_called=False
            ) as mock:
                mock.post("/tweets").mock(
                    return_value=httpx.Response(
                        200, json={"data": {"id": f"id-{i}"}}
                    )
                )
                result = publish_draft_sync(
                    configured_settings, tmp_db, x_client, draft
                )
            assert result.x_tweet_id == f"id-{i}"

        # All three drafts reached the "posted" state in the DB.
        for did, i in zip(ids, range(3), strict=True):
            row = tmp_db.get_draft(did)
            assert row is not None
            assert row.status == "posted"
            assert row.x_tweet_id == f"id-{i}"

    def test_publish_draft_sync_uses_callers_token_manager(
        self, configured_settings, x_client, tmp_db: Database
    ):
        """The one-shot XClient must use the caller's ``token_manager``
        so the same OAuth refresh logic and ``data/oauth_tokens.json``
        file are read for user-context calls."""
        from x_auto.x.publish import publish_draft_sync

        # Stub out the user-context call so the test doesn't need a
        # real X server; the assertion is that the request goes
        # through with the Authorization header our token manager
        # would issue.
        draft = _make_draft(tmp_db, body="auth-test", link_url=None)
        with respx.mock(base_url=API_BASE, assert_all_called=False) as mock:
            route = mock.post("/tweets").mock(
                return_value=httpx.Response(
                    200, json={"data": {"id": "auth-1"}}
                )
            )
            publish_draft_sync(configured_settings, tmp_db, x_client, draft)
        # The first request (main post) used the bearer-or-user
        # header from the caller's token manager.
        sent = route.calls[0].request
        assert sent.headers.get("Authorization", "").startswith("Bearer ")
