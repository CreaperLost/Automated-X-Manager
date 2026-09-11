"""Unified manual fetch and saved-source review view."""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import streamlit as st

from ..ai.client import AIClient
from ..ai.projects import list_projects
from ..ai.workflow import AgentDraftWorkflow
from ..config import Settings, load_accounts
from ..store.models import Draft, Tweet
from ..store.repos import Database
from ..utils.media_catalog import (
    catalog_path_for_data_dir,
    list_project_media_with_descriptions,
)
from ..utils.text import format_cta_reply, validate_post_body, x_char_count
from ..utils.virality import (
    compute_engagement_velocity,
    get_hot_opportunity_ids,
)
from ..x.client import AuthExpiredError, XApiError, XClient
from .tab_create import _cache_source_image
from .tab_fetch import _fetch_all


def render(
    settings: Settings,
    db: Database,
    x_client: XClient,
    ai: AIClient | None = None,
) -> None:
    niche_label = settings.niche.upper()
    niche_title = settings.niche.title()
    st.header(f"Sources · {niche_label}")

    # Check for active Human-in-the-Loop review modal
    review_data = st.session_state.get(f"agent_review_data_{settings.niche}")
    if review_data:
        _render_agent_review_dialog(settings, db, review_data)

    pool = load_accounts(settings.config_dir)
    maximum = len(pool) * (0.010 + settings.x.recent_max_results * 0.005)
    st.caption(
        f"Fetch is always manual. Saved sources below can be reused without "
        f"another X read. Maximum for this fetch: **${maximum:0.3f}** "
        f"({len(pool)} {niche_title} creators × up to {settings.x.recent_max_results} posts)."
    )
    if not pool:
        st.warning(f"No creators configured in `config/{settings.niche}/creators.yaml`.")
    elif st.button("Fetch recent", type="primary", key=f"sources_fetch_{settings.niche}"):
        cost_before = x_client.meter.reads_cost()
        with st.spinner(f"Fetching from X for {niche_title}…"):
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
                    f"{summary['handles_ok']}/{summary['handles_total']} creators. "
                    f"Actual fetch cost: ${fetch_cost:0.4f}."
                )

    all_tweets = db.list_tweets(limit=500)
    used_ids = {
        d.source_tweet_id for d in db.list_drafts(limit=1000) if d.source_tweet_id
    }
    hot_opportunity_ids = get_hot_opportunity_ids(all_tweets)
    counts = {
        "New": sum(t.status == "new" and t.id not in used_ids for t in all_tweets),
        "🔥 Hot": sum(t.id in hot_opportunity_ids for t in all_tweets),
        "Selected": sum(t.status == "selected" for t in all_tweets),
        "Used": sum(t.id in used_ids for t in all_tweets),
        "Archived": sum(t.status == "archived" for t in all_tweets),
    }
    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        search = st.text_input(
            "Search saved sources", placeholder="Search text or @handle…",
            key=f"sources_search_{settings.niche}",
        ).strip().lower()
    with c2:
        selected_filter = st.selectbox(
            "Filter",
            [f"{name} ({count})" for name, count in counts.items()],
            key=f"sources_filter_{settings.niche}",
        ).split(" (", 1)[0]
    with c3:
        sort_mode = st.selectbox(
            "Sort",
            ["Newest", "🔥 Highest Opportunity"],
            key=f"sources_sort_{settings.niche}",
        )

    tweets = _filter(all_tweets, selected_filter, search, used_ids, hot_opportunity_ids)
    if sort_mode == "🔥 Highest Opportunity":
        tweets.sort(
            key=lambda t: compute_engagement_velocity(t.public_metrics, t.created_at),
            reverse=True,
        )

    if not tweets:
        st.info("No saved sources match this filter.")
        return
    for row_start in range(0, len(tweets), 2):
        cols = st.columns(2, gap="small")
        for col, tweet in zip(cols, tweets[row_start:row_start + 2], strict=False):
            with col:
                _card(
                    db,
                    tweet,
                    tweet.id in used_ids,
                    settings,
                    ai=ai,
                    is_hot=tweet.id in hot_opportunity_ids,
                )


def _filter(
    tweets: list[Tweet],
    selected_filter: str,
    search: str,
    used_ids: set[str],
    hot_opportunity_ids: set[str] | None = None,
) -> list[Tweet]:
    if selected_filter == "Used":
        out = [t for t in tweets if t.id in used_ids]
    elif selected_filter == "New":
        out = [t for t in tweets if t.status == "new" and t.id not in used_ids]
    elif selected_filter == "🔥 Hot":
        hots = hot_opportunity_ids or set()
        out = [t for t in tweets if t.id in hots]
    else:
        out = [t for t in tweets if t.status == selected_filter.lower()]
    if search:
        out = [
            t for t in out
            if search in t.text.lower() or search in t.account_handle.lower()
        ]
    return out


def _card(
    db: Database,
    tweet: Tweet,
    used: bool,
    settings: Settings,
    ai: AIClient | None = None,
    is_hot: bool = False,
) -> None:
    likes = (tweet.public_metrics or {}).get("like_count", 0)
    velocity = compute_engagement_velocity(tweet.public_metrics, tweet.created_at)
    with st.container(border=True):
        flags = []
        if is_hot or velocity >= 10.0:
            flags.append(f"🔥 Hot ({velocity:0.1f}/h)")
        if used:
            flags.append("Used")
        if tweet.source_image_url:
            flags.append("Image")
        st.markdown(f"**@{tweet.account_handle}**" + (f" · {' · '.join(flags)}" if flags else ""))
        st.markdown(tweet.text if len(tweet.text) <= 240 else tweet.text[:237].rstrip() + "…")
        st.caption(f"{_date(tweet.created_at)} · ❤ {likes}")

        auto_col, use_col, select_col, archive_col = st.columns([1.6, 1.4, 1, 1])
        with auto_col:
            if st.button("⚡ Auto-create", key=f"source_auto_{tweet.id}", type="primary", use_container_width=True):
                if ai is None or not ai.configured:
                    st.error("AI is not configured. Please set MINIMAX_API_KEY in .env.")
                else:
                    projects = list_projects(db)
                    if not projects:
                        st.error(f"No activations found in data/{settings.niche}/projects.csv.")
                    else:
                        with st.spinner("🤖 Agent analyzing, matching activation, and drafting A/B options…"):
                            try:
                                agent_wf = AgentDraftWorkflow(ai, niche=settings.niche, data_dir=settings.data_dir)
                                result = agent_wf.run(
                                    source_text=tweet.text,
                                    source_author=tweet.account_handle,
                                    source_tweet_id=tweet.id,
                                    projects=projects,
                                )
                            except Exception as exc:
                                st.error(f"Agent failed to generate drafts: {exc}")
                            else:
                                catalog_path = catalog_path_for_data_dir(settings.data_dir)
                                media_files = list_project_media_with_descriptions(
                                    settings.data_dir / "media_cache",
                                    catalog_path,
                                    result.project_name,
                                )
                                st.session_state[f"agent_review_data_{settings.niche}"] = {
                                    "tweet": tweet,
                                    "result": result,
                                    "media_files": media_files,
                                }
                                st.rerun()

        with use_col:
            if st.button("Open Create", key=f"source_use_{tweet.id}", use_container_width=True):
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


# ---- Human-in-the-Loop Review Dialog ----------------------------------------

def _render_review_body(settings: Settings, db: Database, review_data: dict) -> None:
    tweet: Tweet = review_data["tweet"]
    result = review_data["result"]
    media_files: list[dict] = review_data.get("media_files", [])

    niche = settings.niche.lower()
    tone_badge = "🔥 Energetic & Positive" if niche == "crypto" else "⚠️ Fear Mongering"

    st.markdown(f"### 🤖 Agent Draft Review · {settings.niche.upper()}")

    # 1. Matched Project Chip
    if result.project_name:
        chip = (
            f"✓ **Matched Activation:** **{result.project_name}** → `{result.project_url}`  \n"
            f"_Why:_ {result.match_reasoning}  \n"
            f"_Tone Applied:_ {tone_badge}"
        )
        st.success(chip)
    else:
        st.info(f"Tone Applied: {tone_badge}")

    # 2. A/B Copy Selection
    st.markdown("#### 📝 Choose Your Copy (A/B)")
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Option A · Rephrase**")
        st.info(result.option_a_main)
        st.caption(f"{x_char_count(result.option_a_main)}/280 chars · _{result.option_a_reasoning}_")
    with col_b:
        st.markdown("**Option B · Original Take**")
        st.info(result.option_b_main)
        st.caption(f"{x_char_count(result.option_b_main)}/280 chars · _{result.option_b_reasoning}_")

    choice_key = f"agent_choice_{tweet.id}"
    chosen_variant = st.radio(
        "Select copy variant to post",
        ["Option A (Rephrase)", "Option B (Original Take)"],
        key=choice_key,
        horizontal=True,
    )

    initial_text = result.option_a_main if chosen_variant.startswith("Option A") else result.option_b_main
    final_body = st.text_area(
        "Main tweet (editable)",
        value=initial_text,
        key=f"agent_body_edit_{tweet.id}_{chosen_variant[:8]}",
        height=85,
    )
    cc = x_char_count(final_body)
    if cc > 280:
        st.error(f"{cc}/280 characters — please trim {cc - 280} chars.")
    else:
        st.caption(f"{cc}/280 characters")

    # 3. Media Selection with Live Visual Previews
    st.markdown("---")
    st.markdown("#### 🖼️ Media Attachment")

    media_options: list[str] = ["None (Text only)"]
    if tweet.source_image_url:
        media_options.append("Source tweet image")

    media_by_label: dict[str, dict] = {}
    default_index = 0
    rec_filename = getattr(result, "recommended_media_name", None)

    for item in media_files:
        is_rec = bool(rec_filename and item.get("filename") == rec_filename)
        label = f"📁 {item['filename']}"
        if is_rec:
            label += " (🎯 AI Recommended)"
        if item.get("description"):
            label += f" — {item['description']}"
        media_options.append(label)
        media_by_label[label] = item
        if is_rec:
            default_index = len(media_options) - 1

    if rec_filename:
        st.caption(f"🎯 AI auto-matched **{rec_filename}** from your catalog based on the tweet's topic.")

    picked_media_label = st.radio(
        "Choose media to attach",
        media_options,
        index=default_index,
        key=f"agent_media_pick_{tweet.id}",
    )

    # Render Preview of selected media
    if picked_media_label == "Source tweet image" and tweet.source_image_url:
        st.image(tweet.source_image_url, caption="Source post image", width=340)
    elif picked_media_label in media_by_label:
        selected_asset = media_by_label[picked_media_label]
        asset_path = Path(selected_asset["path"])
        if selected_asset.get("is_video"):
            st.video(str(asset_path))
        else:
            st.image(str(asset_path), width=340)
        if selected_asset.get("description"):
            st.caption(f"Catalog description: **{selected_asset['description']}**")

    # 4. CTA Reply Post
    st.markdown("---")
    st.markdown("#### 💬 Reply Post (CTA + Activation Link)")
    reply_text = st.text_input(
        "Reply tweet (sent as a separate post)",
        value=result.cta_text,
        key=f"agent_reply_input_{tweet.id}",
        help="Sent as a separate reply tweet with the project link.",
    )
    r_cc = x_char_count(reply_text)
    if r_cc > 280:
        st.error(f"{r_cc}/280 characters — please trim {r_cc - 280} chars.")
    else:
        st.caption(f"{r_cc}/280 characters")

    if result.project_url and result.project_url not in reply_text:
        st.warning(f"⚠️ Reply currently missing activation link: `{result.project_url}`")

    st.markdown("---")
    c_submit, c_cancel = st.columns([2, 1])
    with c_submit:
        if st.button(
            "🚀 Confirm & Send to Queue",
            type="primary",
            use_container_width=True,
            key=f"agent_confirm_btn_{tweet.id}",
        ):
            # Guarantee project URL is included in the reply if matched
            if result.project_url and result.project_url not in reply_text:
                reply_text = format_cta_reply(reply_text, result.project_url, niche=settings.niche)

            # Preflight validation
            errs: list[str] = []
            for e in validate_post_body(final_body, role="main"):
                errs.append(f"Main post: {e.message}")
            if reply_text:
                for e in validate_post_body(reply_text, role="reply", allow_url=True):
                    errs.append(f"Reply post: {e.message}")

            if errs:
                st.error("Cannot queue draft:\n- " + "\n- ".join(errs))
                return

            # Resolve image paths
            image_paths: list[str] = []
            if picked_media_label == "Source tweet image" and tweet.source_image_url:
                try:
                    image_paths = [_cache_source_image(settings, db, tweet)]
                except Exception as exc:
                    st.error(f"Failed to cache source image: {exc}")
                    return
            elif picked_media_label in media_by_label:
                image_paths = [media_by_label[picked_media_label]["path"]]

            writing_mode = (
                "rephrase" if chosen_variant.startswith("Option A") else "original_take"
            )
            new_draft = Draft(
                source_tweet_id=tweet.id,
                quote_tweet_id=None,
                writing_mode=writing_mode,
                body=final_body.strip(),
                link_url=reply_text.strip() or None,
                image_paths=image_paths,
                tone=niche,
                status="draft",
            )
            draft_id = db.create_draft(new_draft)
            db.set_tweet_status(tweet.id, "selected")

            st.session_state["publish_last_post_result"] = {
                "kind": "success",
                "message": f"Draft #{draft_id} created by Agent and queued successfully!",
            }
            st.session_state["navigation_choice"] = "Queue"
            st.session_state.pop(f"agent_review_data_{settings.niche}", None)
            st.rerun()

    with c_cancel:
        if st.button("Cancel", use_container_width=True, key=f"agent_cancel_btn_{tweet.id}"):
            st.session_state.pop(f"agent_review_data_{settings.niche}", None)
            st.rerun()


def _render_agent_review_dialog(settings: Settings, db: Database, review_data: dict) -> None:
    """Open modal dialog if supported by Streamlit, or render inline fallback."""
    if hasattr(st, "dialog"):
        @st.dialog("⚡ Auto-Create Review", width="large")
        def _modal() -> None:
            _render_review_body(settings, db, review_data)

        _modal()
    else:
        with st.container(border=True):
            _render_review_body(settings, db, review_data)
