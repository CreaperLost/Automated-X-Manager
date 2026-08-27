"""Tab 1 — Fetch recent tweets from the configured handle pool."""
from __future__ import annotations

import asyncio

import streamlit as st

from ..config import Settings
from ..store.repos import Database
from ..x.client import AuthExpiredError, XApiError, XClient


def render(
    settings: Settings,
    db: Database,
    x_client: XClient,
) -> None:
    st.header("Fetch")
    st.caption(
        "Pull recent tweets from the accounts in `config/accounts.yaml`. "
        "Each refresh costs $0.010 per handle (user lookup) + $0.005 per new tweet."
    )

    pool = settings.accounts
    if not pool:
        st.warning("No handles configured. Edit `config/accounts.yaml`.")
        return

    st.markdown("**Handle pool**")
    for a in pool:
        st.markdown(f"- `@{a['handle']}`")

    if st.button("Fetch recent", type="primary"):
        with st.spinner("Fetching from X…"):
            try:
                summary = asyncio.run(_fetch_all(settings, db, x_client, pool))
            except AuthExpiredError as exc:
                st.error(f"Auth error: {exc.detail}")
                return
            except XApiError as exc:
                st.error(f"X API error ({exc.status}): {exc.detail}")
                return
        st.success(
            f"Fetched {summary['new_tweets']} new tweets from "
            f"{summary['handles_ok']}/{summary['handles_total']} handles. "
            f"Cost: ${summary['cost']:0.4f}"
        )
        for handle, count in summary["per_handle"].items():
            st.caption(f"  @{handle}: {count} new")


async def _fetch_all(
    settings: Settings,
    db: Database,
    x_client: XClient,
    pool: list[dict[str, str]],
) -> dict:
    per_handle: dict[str, int] = {}
    handles_ok = 0
    for a in pool:
        handle = a["handle"]
        try:
            user = await x_client.get_user_by_username(handle)
            db.upsert_account(user.username, user.id, user.name)
            tweets = await x_client.get_user_tweets(
                user.id,
                max_results=settings.x.recent_max_results,
                exclude=settings.x.exclude,
            )
            payload = [
                {
                    "id": t.id,
                    "text": t.text,
                    "created_at": t.created_at,
                    "public_metrics": t.public_metrics,
                }
                for t in tweets
            ]
            new_count = db.upsert_tweets(user.username, payload)
            db.mark_account_fetched(user.username)
            per_handle[handle] = new_count
            handles_ok += 1
        except XApiError as exc:
            per_handle[handle] = -1
            st.warning(f"  @{handle}: {exc.status} {exc.detail[:100]}")
    return {
        "new_tweets": sum(c for c in per_handle.values() if c > 0),
        "handles_ok": handles_ok,
        "handles_total": len(pool),
        "cost": x_client.meter.reads_cost(),
        "per_handle": per_handle,
    }
