from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from x_auto.x.auth import TOKEN_URL, TokenBundle, TokenManager, TokenStore


def _bundle(token: str, *, expired: bool, refresh: str = "refresh") -> TokenBundle:
    offset = timedelta(hours=-1 if expired else 1)
    return TokenBundle(
        access_token=token,
        refresh_token=refresh,
        expires_at=datetime.now(UTC) + offset,
        scope="offline.access tweet.write",
    )


def test_manager_reloads_token_written_by_reauthorization(
    configured_settings, tmp_path
):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(_bundle("old", expired=True))
    manager = TokenManager(configured_settings, store=store)
    manager._load_or_raise()

    store.save(_bundle("new", expired=False))

    assert manager.access_token() == "new"


def test_refresh_error_includes_oauth_detail(configured_settings, tmp_path):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(_bundle("expired", expired=True))
    manager = TokenManager(configured_settings, store=store)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                400,
                json={
                    "error": "invalid_grant",
                    "error_description": "The refresh token is invalid.",
                },
            )
        )
        with pytest.raises(RuntimeError, match="invalid_grant"):
            manager.access_token()


def test_refresh_keeps_existing_refresh_token_when_response_omits_it(
    configured_settings, tmp_path
):
    store = TokenStore(tmp_path / "oauth.json")
    store.save(_bundle("expired", expired=True, refresh="keep-me"))
    manager = TokenManager(configured_settings, store=store)

    with respx.mock(assert_all_called=True) as mock:
        mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "fresh",
                    "expires_in": 7200,
                    "scope": "offline.access tweet.write",
                },
            )
        )
        assert manager.access_token() == "fresh"

    saved = store.load()
    assert saved is not None
    assert saved.refresh_token == "keep-me"
