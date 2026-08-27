"""Thin async httpx wrapper around the four X API v2 endpoints we use.

Endpoints (verified Aug 2026):
  GET  /2/users/by/username/:handle        Bearer  $0.010
  GET  /2/users/:id/tweets                Bearer  $0.005 * N
  POST /2/tweets                          User    $0.015 (plain) | $0.200 (URL inline)
  DELETE /2/tweets/:id                    User    $0.010
  POST /2/media/upload                    User    free
  GET  /2/users/me                        User    $0.010

The client surfaces rate-limit info from response headers
(x-rate-limit-remaining, x-rate-limit-reset) and raises typed
exceptions on 429 / 401. Centralised retry: 1 retry on 5xx, 1
retry on 429 after honoring the reset header.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ..config import Settings, get_settings
from .auth import TokenManager
from .costs import (
    COST_POST_DELETED,
    COST_POST_PLAIN,
    SessionMeter,
)

API_BASE = "https://api.x.com/2"
USER_AGENT = "X-Automation/0.1.0 (+https://docs.x.com)"

# Type aliases for clarity.
PostId = str
UserId = str
Username = str


class XApiError(Exception):
    """Base exception for X API errors with a status code and detail."""

    def __init__(self, status: int, detail: str, *, url: str | None = None) -> None:
        super().__init__(f"X API {status} on {url or '?'}: {detail}")
        self.status = status
        self.detail = detail
        self.url = url


class RateLimitedError(XApiError):
    """Raised after the second 429 in a row (after one retry)."""

    def __init__(self, retry_after_seconds: int, url: str | None = None) -> None:
        super().__init__(429, f"rate limited, retry in {retry_after_seconds}s", url=url)
        self.retry_after_seconds = retry_after_seconds


class AuthExpiredError(XApiError):
    """Raised when the user-context token cannot be refreshed."""


@dataclass
class RateLimitInfo:
    limit: int
    remaining: int
    reset_unix: int

    @property
    def seconds_to_reset(self) -> int:
        return max(0, self.reset_unix - int(time.time()))


@dataclass
class Tweet:
    id: PostId
    text: str
    author_id: UserId
    created_at: str  # ISO 8601
    public_metrics: dict[str, int]


@dataclass
class User:
    id: UserId
    username: Username
    name: str


def _parse_rate_limit_headers(headers: httpx.Headers) -> RateLimitInfo | None:
    try:
        return RateLimitInfo(
            limit=int(headers.get("x-rate-limit-limit", 0)),
            remaining=int(headers.get("x-rate-limit-remaining", 0)),
            reset_unix=int(headers.get("x-rate-limit-reset", 0)),
        )
    except (TypeError, ValueError):
        return None


def _should_retry(status: int) -> bool:
    return status in (429, 500, 502, 503, 504)


class XClient:
    """Async X API client. Single instance per process.

    Reads the bearer token from settings.x.bearer_token; user-context
    access tokens come from the TokenManager.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        token_manager: TokenManager | None = None,
        meter: SessionMeter | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._tokens = token_manager or TokenManager(self._settings)
        self._meter = meter or SessionMeter()
        self._http = client or httpx.AsyncClient(
            base_url=API_BASE,
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    @property
    def meter(self) -> SessionMeter:
        return self._meter

    @property
    def tokens(self) -> TokenManager:
        return self._tokens

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> XClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ----- Internal request helper -----
    async def _request(
        self,
        method: str,
        path: str,
        *,
        auth: Literal["bearer", "user"] = "bearer",
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: Any = None,
    ) -> tuple[dict[str, Any], RateLimitInfo | None]:
        headers: dict[str, str] = {}
        if auth == "bearer":
            token = self._settings.x.bearer_token
            if not token:
                raise AuthExpiredError(401, "X_BEARER_TOKEN is not configured", url=path)
            headers["Authorization"] = f"Bearer {token}"
        else:
            try:
                token = self._tokens.access_token()
            except RuntimeError as exc:
                raise AuthExpiredError(401, str(exc), url=path) from exc
            headers["Authorization"] = f"Bearer {token}"

        last_exc: XApiError | None = None
        for attempt in range(2):
            try:
                resp = await self._http.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    data=data,
                    files=files,
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                if attempt == 1:
                    raise XApiError(0, f"network error: {exc}", url=path) from exc
                last_exc = XApiError(0, f"network error: {exc}", url=path)
                await asyncio.sleep(1.0)
                continue

            rl = _parse_rate_limit_headers(resp.headers)
            if resp.status_code < 400:
                return resp.json(), rl

            detail = resp.text[:500] if resp.text else "(no body)"
            err = XApiError(resp.status_code, detail, url=path)
            if resp.status_code == 401 and auth == "user":
                # Try one refresh + retry.
                if attempt == 0:
                    last_exc = err
                    # Invalidate the cached token so the next call refreshes.
                    self._tokens._cached = None  # type: ignore[attr-defined]
                    continue
                raise AuthExpiredError(401, detail, url=path)
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("retry-after", "5"))
                if attempt == 0:
                    last_exc = RateLimitedError(retry_after, url=path)
                    await asyncio.sleep(min(retry_after, 30))
                    continue
                raise RateLimitedError(retry_after, url=path)
            if _should_retry(resp.status_code):
                if attempt == 0:
                    last_exc = err
                    await asyncio.sleep(1.0)
                    continue
                raise err
            raise err
        if last_exc:
            raise last_exc
        raise XApiError(0, "request failed without response", url=path)

    # ----- Public methods (the four endpoints) -----
    async def get_user_by_username(self, username: Username) -> User:
        """GET /2/users/by/username/:username ($0.010)."""
        handle = username.lstrip("@")
        body, _ = await self._request(
            "GET", f"/users/by/username/{handle}", auth="bearer"
        )
        self._meter.add_read_profile(1)
        d = body.get("data", {})
        return User(id=str(d.get("id", "")), username=str(d.get("username", handle)),
                    name=str(d.get("name", "")))

    async def get_user_tweets(
        self,
        user_id: UserId,
        *,
        max_results: int = 20,
        exclude: tuple[str, ...] = ("replies", "retweets"),
    ) -> list[Tweet]:
        """GET /2/users/:id/tweets ($0.005 * N)."""
        params: dict[str, Any] = {
            "max_results": max(5, min(100, max_results)),
            "tweet.fields": "created_at,public_metrics,author_id",
        }
        if exclude:
            params["exclude"] = ",".join(exclude)
        body, _ = await self._request(
            "GET", f"/users/{user_id}/tweets", auth="bearer", params=params
        )
        tweets: list[Tweet] = []
        for t in body.get("data", []) or []:
            tweets.append(
                Tweet(
                    id=str(t["id"]),
                    text=str(t.get("text", "")),
                    author_id=str(t.get("author_id", user_id)),
                    created_at=str(t.get("created_at", "")),
                    public_metrics=dict(t.get("public_metrics", {})),
                )
            )
        self._meter.add_read_post(len(tweets))
        return tweets

    async def get_me(self) -> User:
        """GET /2/users/me ($0.010)."""
        body, _ = await self._request("GET", "/users/me", auth="user")
        d = body.get("data", {})
        return User(id=str(d.get("id", "")), username=str(d.get("username", "")),
                    name=str(d.get("name", "")))

    async def create_post(
        self,
        text: str,
        *,
        media_ids: list[str] | None = None,
        reply_to: PostId | None = None,
    ) -> PostId:
        """POST /2/tweets ($0.015 plain | $0.200 URL inline)."""
        body_json: dict[str, Any] = {"text": text}
        if media_ids:
            body_json["media"] = {"media_ids": media_ids}
        if reply_to:
            body_json["reply"] = {"in_reply_to_tweet_id": reply_to}
        body, _ = await self._request("POST", "/tweets", auth="user", json_body=body_json)
        # Cost: we treat it as plain; the UI/UI layer is responsible for
        # not allowing URL-containing text into the main body.
        self._meter.add_write(COST_POST_PLAIN)
        return str(body["data"]["id"])

    async def delete_post(self, post_id: PostId) -> bool:
        """DELETE /2/tweets/:id ($0.010)."""
        await self._request("DELETE", f"/tweets/{post_id}", auth="user")
        self._meter.add_write(COST_POST_DELETED)
        return True
