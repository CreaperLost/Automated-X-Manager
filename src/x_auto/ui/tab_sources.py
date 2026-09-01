"""Unified manual fetch and saved-source review view."""
from __future__ import annotations

import asyncio
from datetime import datetime

import streamlit as st

from ..config import Settings, load_accounts
from ..store.models import Tweet
from ..store.repos import Database
from ..x.client import AuthExpiredError, XApiError, XClient
from .tab_fetch import _fetch_all


def render(settings: Settings, db: Database, x_client: XClient) -> None:
    st.header("Sources")
    pool = load_accounts(settings.config_dir)
    maximum = len(pool) * (0.010 + settings.x.recent_max_results * 0.005)
    st.caption(
        "Fetch is always manual. Saved sources below can be reused without "
        f"another X read. Maximum for this fetch: **${maximum:0.3f}** "
        f"({len(pool)} handles × up to {settings.x.recent_max_results} posts)."
    )
    if not pool:
        st.warning("No handles configured in `config/accounts.yaml`.")
    elif st.button("Fetch recent", type="primary", key="sources_fetch"):
        cost_before = x_client.meter.reads_cost()
        with st.spinner("Fetching from X…"):
            try:
                summary = asyncio.run(_fetch_all(settings, db, x_client, pool))
            except AuthExpiredError as exc:
                st.error(f"Auth error: {exc.detail}")
            except XApiError as exc:
                st.error(f"X API error ({exc.status}): {exc.detail}")
            else:
                fetch_cost = max(0.0, summary["cost"] - cost_before)
                st.success(
                    f"Fetched {summary['new_tweets']} new posts from "
                    f"{summary['handles_ok']}/{summary['handles_total']} handles. "
                    f"Actual fetch cost: ${fetch_cost:0.4f}."
                )

    all_tweets = db.list_tweets(limit=500)
    used_ids = {
        d.source_tweet_id for d in db.list_drafts(limit=1000) if d.source_tweet_id
    }
    counts = {
        "New": sum(t.status == "new" and t.id not in used_ids for t in all_tweets),
        "Selected": sum(t.status == "selected" for t in all_tweets),
        "Used": sum(t.id in used_ids for t in all_tweets),
        "Archived": sum(t.status == "archived" for t in all_tweets),
    }
    c1, c2 = st.columns([2, 1])
    with c1:
        search = st.text_input(
            "Search saved sources", placeholder="Search text or @handle…",
            key="sources_search",
        ).strip().lower()
    with c2:
        selected_filter = st.selectbox(
            "Filter",
            [f"{name} ({count})" for name, count in counts.items()],
            key="sources_filter",
        ).split(" (", 1)[0]

    tweets = _filter(all_tweets, selected_filter, search, used_ids)
    if not tweets:
        st.info("No saved sources match this filter.")
        return
    for row_start in range(0, len(tweets), 2):
        cols = st.columns(2, gap="small")
        for col, tweet in zip(cols, tweets[row_start:row_start + 2], strict=False):
            with col:
                _card(db, tweet, tweet.id in used_ids)


def _filter(
    tweets: list[Tweet], selected_filter: str, search: str, used_ids: set[str]
) -> list[Tweet]:
    if selected_filter == "Used":
        out = [t for t in tweets if t.id in used_ids]
    elif selected_filter == "New":
        out = [t for t in tweets if t.status == "new" and t.id not in used_ids]
    else:
        out = [t for t in tweets if t.status == selected_filter.lower()]
    if search:
        out = [
            t for t in out
            if search in t.text.lower() or search in t.account_handle.lower()
        ]
    return out


def _card(db: Database, tweet: Tweet, used: bool) -> None:
    likes = (tweet.public_metrics or {}).get("like_count", 0)
    with st.container(border=True):
        flags = []
        if used:
            flags.append("Used")
        if tweet.source_image_url:
            flags.append("Image")
        st.markdown(f"**@{tweet.account_handle}**" + (f" · {' · '.join(flags)}" if flags else ""))
        st.markdown(tweet.text if len(tweet.text) <= 240 else tweet.text[:237].rstrip() + "…")
        st.caption(f"{_date(tweet.created_at)} · ❤ {likes}")
        use_col, select_col, archive_col = st.columns([1.5, 1, 1])
        with use_col:
            if st.button("Select & open Create", key=f"source_use_{tweet.id}", use_container_width=True):
                db.set_tweet_status(tweet.id, "selected")
                st.session_state["create_selected_source_id"] = tweet.id
                st.session_state["requested_view"] = "Create"
                st.rerun()
        with select_col:
            label = "Unselect" if tweet.status == "selected" else "Select"
            if st.button(label, key=f"source_select_{tweet.id}", use_container_width=True):
                db.set_tweet_status(tweet.id, "new" if tweet.status == "selected" else "selected")
                st.rerun()
        with archive_col:
            label = "Restore" if tweet.status == "archived" else "Archive"
            if st.button(label, key=f"source_archive_{tweet.id}", use_container_width=True):
                db.set_tweet_status(tweet.id, "new" if tweet.status == "archived" else "archived")
                st.rerun()


def _date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d") if value else "—"
