"""Tab 2 — Review fetched tweets and select the ones to use as inspiration.

Layout: three sub-tabs (New / Selected / Archived), each a 2-up grid
of tweet cards. Each card has a single action button — click it to
move that one tweet to the next status. No checkboxes, no bulk
action, no multi-select.

2 columns is a universal default; the 3-up variant is a future
option once CSS-driven responsive columns land.

Each card shows the handle, tweet text (truncated), a relative
timestamp, and a like count. The full text and metrics are still
in the DB; the card just keeps the most-relevant fields visible
at a glance.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from ..store.models import Tweet
from ..store.repos import Database

# Sub-tab actions (label + target status). Reused as the per-card
# button label and the DB target, so they can't drift.
ACTIONS = {
    "new":      ("Select",   "selected"),
    "selected": ("Unselect", "new"),
    "archived": ("Restore",  "new"),
}


def render(db: Database) -> None:
    st.header("Review")
    st.caption(
        "Click **Select** on a card to move it to the Selected tab. "
        "From there you can **Unselect** (back to New) or it can be "
        "**Archived** later."
    )

    new_tweets = db.list_tweets(status="new", limit=200)
    selected = db.list_tweets(status="selected", limit=200)
    archived = db.list_tweets(status="archived", limit=200)

    tab_new, tab_sel, tab_arc = st.tabs(
        [
            f"New ({len(new_tweets)})",
            f"Selected ({len(selected)})",
            f"Archived ({len(archived)})",
        ]
    )

    with tab_new:
        _render_subtab(db, new_tweets, source="new")
    with tab_sel:
        _render_subtab(db, selected, source="selected")
    with tab_arc:
        _render_subtab(db, archived, source="archived")


def _render_subtab(db: Database, tweets: list[Tweet], *, source: str) -> None:
    """Render one Review sub-tab: 2-col grid, one button per card."""
    if not tweets:
        st.info("Nothing here yet.")
        return

    action_label, target_status = ACTIONS[source]
    st.caption(f"{len(tweets)} tweet(s). Click an action to move it.")

    for row_start in range(0, len(tweets), 2):
        chunk = tweets[row_start:row_start + 2]
        cols = st.columns(2, gap="small")
        for col, t in zip(cols, chunk, strict=False):
            with col:
                _render_tweet_card(db, t, source=source)


def _render_tweet_card(
    db: Database, tweet: Tweet, *, source: str
) -> None:
    """A single 1/2-width tweet card with one action button."""
    action_label, target_status = ACTIONS[source]
    likes = tweet.public_metrics.get("like_count", 0) if tweet.public_metrics else 0

    with st.container(border=True):
        st.markdown(f"**@{tweet.account_handle}**")
        st.markdown(_truncate(tweet.text, 200))

        st.caption(
            f"🕐 {_fmt_dt(tweet.created_at)} · ❤ {likes}"
        )

        if st.button(
            action_label,
            key=f"review_card_{source}_{tweet.id}",
            use_container_width=True,
        ):
            db.set_tweet_statuses([tweet.id], target_status)
            st.rerun()


# ---- helpers ----------------------------------------------------------------

def _fmt_dt(value: datetime) -> str:
    """Relative time for events <7 days, absolute date for older.

    Handles both naive and tz-aware inputs: the test fixtures use
    ``datetime(..., tzinfo=UTC)`` while the SQLite path returns naive
    datetimes. We always subtract two values of the same kind.
    """
    if not value or value.year <= 1:
        return "—"
    if value.tzinfo is not None:
        from datetime import UTC

        now = datetime.now(UTC)
    else:
        now = datetime.now()
    delta_s = (now - value).total_seconds()
    if delta_s < 0:
        return value.strftime("%Y-%m-%d %H:%M")
    if delta_s < 60:
        return "now"
    if delta_s < 3600:
        return f"{int(delta_s // 60)}m ago"
    if delta_s < 86_400:
        return f"{int(delta_s // 3600)}h ago"
    if delta_s < 86_400 * 7:
        return f"{int(delta_s // 86_400)}d ago"
    return value.strftime("%Y-%m-%d")


def _truncate(text: str, max_chars: int) -> str:
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "…"
