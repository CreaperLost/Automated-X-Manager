"""Shared "post a draft" workflow.

Queue and Create both need to upload attached media, create the main
post, create the reply (if there is a project link), update the draft
row, and log the post. This module centralises that flow.

Two entry points:

* :func:`publish_draft` (async) — the canonical implementation.

* :func:`publish_draft_sync` — a thin ``asyncio.run`` wrapper for
  Streamlit, which is synchronous and can't ``await`` directly. It
  builds a one-shot ``XClient`` with a fresh ``httpx.AsyncClient``
  so the connection pool is bound to the transient loop that
  ``asyncio.run`` creates and tears down. The shared ``x_client``'s
  persistent httpx client would otherwise be tied to whichever loop
  happened to be current when ``XClient`` was first constructed,
  and a second ``asyncio.run`` call would close that loop under it
  and raise ``RuntimeError: Event loop is closed`` (Python 3.14,
  anyio/httpx).

Both update the draft's status, set ``x_tweet_id`` / ``x_reply_id`` /
``cost_usd``, and write a row to ``post_log``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from ..config import Settings
from ..store.models import Draft
from ..store.repos import Database
from ..utils.files import is_video_path
from ..utils.text import validate_post_body
from .client import API_BASE, USER_AGENT, XClient
from .costs import estimate_post_cost
from .media import upload_media_cached


@dataclass
class PublishResult:
    """What a successful publish looks like, for the UI / log."""

    x_tweet_id: str
    x_reply_id: str | None
    cost_usd: float


class PublishValidationError(ValueError):
    """A deterministic local rule failed before any X write."""


async def publish_draft(
    settings: Settings,
    db: Database,
    x_client,  # XClient (any IO-capable instance with create_post)
    draft: Draft,
) -> PublishResult:
    """Post ``draft`` as a main + reply thread on X. Updates the row.

    Raises whatever :class:`XApiError` / :class:`AuthExpiredError` the
    underlying X client raises; the caller decides how to surface
    them.
    """
    errors = validate_post_body(draft.body, role="main")
    if draft.link_url:
        errors.extend(
            validate_post_body(draft.link_url, role="reply", allow_url=True)
        )
    if errors:
        raise PublishValidationError("; ".join(error.message for error in errors))

    # X rejects API quotes of arbitrary third-party posts with 403 unless the
    # authenticated user authored or was mentioned in the quoted post. Sources
    # in this app are monitored third-party accounts, so legacy quote IDs are
    # inspiration metadata only and must not be included in the write payload.
    if draft.quote_tweet_id:
        draft.quote_tweet_id = None
        db.update_draft(draft)

    video_count = sum(is_video_path(path) for path in draft.image_paths)
    if video_count and len(draft.image_paths) != 1:
        raise PublishValidationError("A video must be the only media attachment.")

    # Upload any attached media, reusing cached media_ids when fresh.
    media_ids: list[str] = []
    for p in draft.image_paths:
        path = Path(p)
        if not path.exists():
            # Tolerate a relative path that lives under the data dir.
            alt = settings.data_dir / p
            if alt.exists():
                path = alt
        media_ids.append(
            upload_media_cached(path, token_manager=x_client.tokens, db=db)
        )

    # Main post (no inline URL — that's the whole point of the
    # main+reply split).
    x_tweet_id = await x_client.create_post(
        draft.body,
        media_ids=media_ids or None,
    )

    # Reply post carries the project link in a separate (cheap) tweet.
    x_reply_id: str | None = None
    if draft.link_url:
        x_reply_id = await x_client.create_post(
            draft.link_url, reply_to=x_tweet_id
        )

    cost = estimate_post_cost(
        draft.body,
        has_image=bool(media_ids),
        link_in_reply=bool(x_reply_id),
        reply_text=draft.link_url or "",
    )

    now = datetime.now()
    draft.status = "posted"
    draft.posted_at = now
    draft.x_tweet_id = x_tweet_id
    draft.x_reply_id = x_reply_id
    draft.cost_usd = cost.total
    db.update_draft(draft)
    db.log_post(
        draft.id,
        "post_now",
        cost.total,
        "success",
        f"x_tweet_id={x_tweet_id} x_reply_id={x_reply_id or ''}",
    )

    return PublishResult(
        x_tweet_id=x_tweet_id,
        x_reply_id=x_reply_id,
        cost_usd=cost.total,
    )


def publish_draft_sync(
    settings: Settings,
    db: Database,
    x_client,
    draft: Draft,
) -> PublishResult:
    """Synchronous wrapper around :func:`publish_draft` for Streamlit.

    Streamlit is sync and can't ``await`` directly, so we wrap the
    coroutine in :func:`asyncio.run`. That creates a fresh event loop
    per call and closes it on return — which leaves the long-lived
    ``x_client._http`` (httpx ``AsyncClient``) holding connections
    bound to the previous loop. The next call then fails with
    ``RuntimeError: Event loop is closed`` when httpx tries to
    run connection cleanup on the closed loop.

    Fix: build a one-shot ``XClient`` that shares the auth state
    (``token_manager``) and the session meter with the long-lived
    instance, but uses a fresh ``httpx.AsyncClient``. The fresh
    client lives for the duration of one ``asyncio.run`` and is
    closed inside the coroutine, before the loop closes.
    """
    fresh_http = httpx.AsyncClient(
        base_url=API_BASE,
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    one_shot = XClient(
        settings,
        token_manager=x_client.tokens,
        meter=x_client.meter,
        client=fresh_http,
    )

    async def _run() -> PublishResult:
        try:
            return await publish_draft(settings, db, one_shot, draft)
        finally:
            await one_shot.aclose()

    return asyncio.run(_run())
